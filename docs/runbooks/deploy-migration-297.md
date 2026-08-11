# Deploy Runbook: Migration 297 — Corporate RPC Ride Idempotency

**Scope:** `297_corporate_rpc_ride_idempotency.sql`
**Domain:** corporate billing (`corporate_wallet_apply_delta`, `corporate_allowance_apply_delta`)
**Estimated prod window:** sub-second (function redefinition only, no table rewrite, no data migration).

---

## 1. Why this one needs a coordinated deploy

`corporate_allowance_apply_delta` gains a new `p_ride_id UUID DEFAULT NULL` parameter. PostgREST
resolves RPC calls by exact named-parameter match — a JSON body carrying a key the target function
doesn't declare fails to resolve (`function ... does not exist`), it does not get silently ignored.

`services/corporate_allowance_service.py`'s `apply_ride_debit` / `apply_ride_debit_reversal` (the
functions `settle_corporate` calls on every corporate ride settlement) always send a real `ride_id`
once the new backend code is live. Until migration 297 has been applied to a given Supabase
instance, **every corporate ride settlement against that instance will fail** if the new backend
code is already deployed there. (`apply_grant`/`apply_reset`/`apply_rollback` are unaffected either
way — the Python layer only includes the `p_ride_id` key when a caller actually passes one.)

This repo's Fly deploy (`deploy-fly.yml`) triggers automatically on push to `main` for any
`backend/**` change. The migration runner (`apply-supabase-schema.yml`) is `workflow_dispatch`-only.
**There is no automated ordering between them** — merging the migration and the backend code in the
same push does not guarantee the migration lands first.

## 2. Mandatory sequence

```
Migration 297 (apply to Supabase)  →  Backend deploy (Fly / Railway)
```

```bash
export SUPABASE_URL=<target-env-url>
export SUPABASE_SERVICE_ROLE_KEY=<target-env-service-role-key>
python backend/scripts/migrate.py --dry-run   # confirm 297 shows as "Would apply"
python backend/scripts/migrate.py             # apply for real
```

Only once this has succeeded against **every environment the backend deploy will reach** (Fly
primary + Railway standby both point at the same Supabase project per
`docs/adr/007-fly-primary-railway-standby.md`, so one apply covers both — but confirm this hasn't
changed before assuming it) should the backend code merge/deploy.

## 3. Verify before deploying backend code

```sql
SELECT version, applied_at FROM schema_migrations
WHERE version = '297_corporate_rpc_ride_idempotency.sql';
-- Expected: 1 row, recent timestamp

-- Confirm the new parameter and output column exist:
SELECT p.parameter_name, p.data_type
FROM information_schema.parameters p
JOIN information_schema.routines r ON r.specific_name = p.specific_name
WHERE r.routine_name = 'corporate_allowance_apply_delta'
ORDER BY p.ordinal_position;
-- Expected: includes p_ride_id (uuid) and a deduped output column
```

## 4. If the backend somehow deployed first (recovery)

Symptom: corporate ride settlements start failing with a Postgres/PostgREST "function
corporate_allowance_apply_delta(...) does not exist" error, visible in Sentry under
`domain=corporate` and in `payment_service.settle_corporate` error logs.

Fix: apply migration 297 immediately (§2) — this closes the gap the moment it lands, no backend
restart required (PostgREST/Supabase re-resolves the function on the next call). No data was
corrupted in this window — `settle_corporate`'s `try/except` around the allowance debit routes to
the master-wallet fallback path on any exception, so rides settle via master debit instead of
allowance debit during the gap, not left unpaid. Once 297 lands, subsequent rides resume normal
allowance-first routing.

## 5. Rollback

See migration 297's own header comment for the full DROP FUNCTION + re-apply-prior-body SQL.
**Roll back the backend code (or at least the ride_id-passing call sites) before or together with**
the database rollback — reverting the DB function first while ride-settlement code still sends
`p_ride_id` reintroduces the same failure in the opposite direction (§1).

## References

- `backend/migrations/297_corporate_rpc_ride_idempotency.sql`
- `backend/services/corporate_allowance_service.py`, `backend/services/corporate_wallet_service.py`
- `backend/services/payment_service.py` — `settle_corporate`
- `docs/change-log/2026-08-11-corporate-rpc-ride-idempotency.md`
- `docs/runbooks/deploy-migration-64-65.md` — precedent for a mandatory-sequence migration pair
