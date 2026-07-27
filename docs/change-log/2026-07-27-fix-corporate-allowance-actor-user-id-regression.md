# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-27 |
| Author | Claude Code (A4 backend test debt pass) |
| Surface(s) | backend, migrations |
| Domain (Sentry tag) | corporate |
| PR / commit link | srikumarimuddana-lab/spinrvm#2421, commit af6ae2d |
| Related issue or gap ID | A4 (156 failing backend tests) — surfaced by `test_actor_user_id_is_text_not_uuid` |

## 1. Issue / gap identified

`corporate_allowance_apply_delta`'s `p_actor_user_id` RPC parameter is UUID
again, even though migration 214 explicitly widened it to TEXT. Any call
into this function passing a platform-admin actor id (e.g. `"admin-001"`,
not a UUID) would raise Postgres `22P02` (invalid_text_representation) and
fail.

## 2. Root cause

Migration 214 widened both `corporate_wallet_transactions.actor_user_id`
and the `corporate_allowance_apply_delta`/`corporate_wallet_apply_delta`
RPC parameters from UUID to TEXT, specifically because the admin manual
wallet-adjust flow records a non-UUID admin id as the actor. Two later
migrations — 248 (`corporate_allowance_ride_debit`) and 258
(allowance-cap guard) — each re-declared `corporate_allowance_apply_delta`
via `CREATE OR REPLACE FUNCTION` for unrelated reasons (adding the
ride-debit ceiling check) and, in doing so, copied forward the function's
original (pre-214) signature with `p_actor_user_id UUID DEFAULT NULL`
instead of preserving 214's `TEXT` widening. Neither migration touched
`corporate_wallet_transactions.actor_user_id` itself (still TEXT), so only
this one RPC parameter regressed. This is exactly the class of bug
`test_actor_user_id_is_text_not_uuid` was written to guard against
(explicitly named in its own docstring), but the guard test itself had
drifted out of the failing-test backlog and wasn't being run/enforced.

## 3. Fix / remediation

New migration `261_corporate_allowance_actor_user_id_text.sql`:
1. `DROP FUNCTION IF EXISTS corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, UUID, TEXT, NUMERIC)` — removes the old UUID-signature overload. (`CREATE OR REPLACE` only replaces a function whose argument-type signature matches exactly; a type change creates a new overload instead of replacing the old one, which would have left both live.)
2. Re-applies migration 258's function body verbatim, with `p_actor_user_id` changed back to `TEXT DEFAULT NULL`.
3. Re-applies the `REVOKE EXECUTE ... FROM PUBLIC, anon, authenticated` / `GRANT EXECUTE ... TO service_role` lockdown for the new signature (the DROP above also dropped the old signature's grants; a fresh function defaults to `PUBLIC` execute).
4. Corrects `SET search_path` from `public, pg_temp` (carried forward unfixed since 248) to `public, pg_catalog`, matching the convention used elsewhere and avoiding a `pg_temp` object-shadowing risk on this `SECURITY DEFINER` function.

Reviewed twice by the `spinr-migration-reviewer` subagent. First pass
flagged both the missing `DROP FUNCTION` and the missing `REVOKE`/`GRANT`
as blockers; second pass after the fix returned `VERDICT: SAFE TO APPLY`
with no remaining issues.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to `corporate_allowance_apply_delta`. Grepped
  the backend for every caller: `services/corporate_allowance_service.py`
  is the only Python caller (via `db_supabase.supabase.rpc(...)`), used
  for `allowance_grant`, `allowance_reset`, `ride_debit`, and
  `ride_debit_reversal` on the corporate per-member allowance path.
  `corporate_wallet_apply_delta` (the sibling master-wallet function) is
  untouched by this migration — its TEXT signature from 214 was never
  regressed (248/258 only redeclared the allowance function, not the
  wallet one).
- **What else reads/writes the same table/function**: `corporate_wallet_transactions` (INSERT only, from inside the RPC — its `actor_user_id` column was already TEXT and is unaffected), `corporate_wallets` and `corporate_member_allowances` (both `UPDATE ... WHERE id = ...` under `FOR UPDATE` locks, unchanged by this migration).
- **Could this regress a currently-working flow?** No known currently-working flow depended on `p_actor_user_id` being UUID — a UUID actor id (e.g. a driver/rider `user_id`) still satisfies a TEXT parameter with no cast needed on the Postgres side, and `db_supabase`'s RPC call passes `actor_user_id` as a plain string already. The only flow this could regress is one that was *relying on* the 22P02 rejection, which is not a real use case.
- **Interaction with background loops / ride state machine / money deltas**: this is money-adjacent (corporate allowance debit/credit), but the fix restores previously-intended, previously-tested behavior (migration 214's) rather than introducing new logic. The row-locking (`FOR UPDATE`, deterministic master-then-allowance lock order) and the allowance-cap guard added by 258 are both preserved verbatim.

## 5. User-experience effect

None directly visible to riders or drivers. For corporate admins: the
admin manual wallet-adjust flow (and any other backend code path calling
`corporate_allowance_apply_delta` with a non-UUID admin actor id) would
previously fail with a raw DB error; after this fix it succeeds as
intended. Not visible mid-session to a rider/driver already using the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/261_corporate_allowance_actor_user_id_text.sql` | New migration: DROP old UUID-signature `corporate_allowance_apply_delta`, CREATE OR REPLACE with `p_actor_user_id TEXT`, re-apply EXECUTE lockdown, correct `search_path` | Restore migration 214's TEXT widening, silently undone by migrations 248 and 258 |
| `backend/tests/test_corporate_b2b_schema.py` | No code change — `test_actor_user_id_is_text_not_uuid` now passes given the migration fix | Confirms the fix; test itself needed no edit |

## 7. Before / after

```sql
-- Before (migration 258, still live prior to this fix)
CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta(
    ...
    p_actor_user_id      UUID DEFAULT NULL,
    ...
)
...
SET search_path = public, pg_temp
```

```sql
-- After (migration 261)
DROP FUNCTION IF EXISTS corporate_allowance_apply_delta(
    UUID, UUID, UUID, TEXT, NUMERIC, UUID, TEXT, NUMERIC);

CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta(
    ...
    p_actor_user_id      TEXT DEFAULT NULL,
    ...
)
...
SET search_path = public, pg_catalog
...
REVOKE EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC)
    TO service_role;
```

## 8. Rollback plan

`DROP FUNCTION IF EXISTS corporate_allowance_apply_delta(UUID, UUID, UUID,
TEXT, NUMERIC, TEXT, TEXT, NUMERIC);` then re-apply migration 258's
`CREATE OR REPLACE FUNCTION` body verbatim (`p_actor_user_id` back to
`UUID DEFAULT NULL`) plus its implicit `PUBLIC` execute grant (i.e., skip
the REVOKE/GRANT block). This is documented in the migration's own
top-of-file rollback comment. No data migration/backfill involved — this
is a function-signature-only change, not a table/column change, and no
rows need remediation.

## 9. Verification performed

- [x] Automated tests run: `pytest -q --no-cov tests/test_corporate_b2b_schema.py::test_actor_user_id_is_text_not_uuid` passes with the migration file present (the test scans migration SQL text directly, no DB connection required)
- [ ] Manual repro steps followed in staging — not done; migration not yet applied to any live Supabase instance, will run via the normal `migrate.py` deploy path
- [x] Blast-radius grep performed: grepped for every caller of `corporate_allowance_apply_delta` (single Python call site in `services/corporate_allowance_service.py`) and every file referencing `actor_user_id` across `migrations/`
- [x] Reviewed against relevant `CLAUDE.md` convention: migration numbering/append-only/RLS/money-function-safety, via two passes of the `spinr-migration-reviewer` subagent (first pass caught 2 real blockers, both fixed and confirmed resolved on re-review)
- [ ] Feature-flagged — not applicable; this is a bug fix restoring already-intended (migration 214) behavior, not new user-facing functionality

## 10. Sign-off

- [x] Rollback plan is concrete and testable (documented DROP + re-apply-258 sequence)
- [x] Blast radius is stated, not assumed (isolated to one RPC function and its single Python caller)
- [x] No silent behavior change to an already-shipped user-facing flow (backend/admin-only, no rider/driver-visible change; "User experience effect" field filled in above)
