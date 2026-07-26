# Runbook — PII Retention Purge

**Owner:** `backend` · **Cadence:** Automatic daily; manual on demand
**Closes:** B-P1-6 (Saskatchewan Transportation Act + PIPEDA retention)

---

## Why This Matters

The Saskatchewan Transportation Act and SGI insurance audit set **maximum**
retention windows for ride-share data. Going beyond those windows is a
compliance finding on first audit. PIPEDA layers on top: PII no longer
needed for its stated purpose must be destroyed or anonymized — "we never
built a purge job" is not a defence.

The retention purge job (`backend/utils/retention_purge.py`, calling
SQL function `purge_pii_retention()` from migration 50) enforces these
windows automatically. It runs once per day at ~03:00 UTC on every
backend replica, gated by a Redis leader lock so only one replica
actually mutates data per cycle. The SQL function is naturally
idempotent — multiple invocations on the same day are safe; the second
finds zero matching rows.

---

## Retention Policy (current)

| Data | Window | Action at window | Rationale |
|---|---|---|---|
| `rides.pickup_lat/lng`, `dropoff_lat/lng`, polylines, `route_snapshot_url` | 3 years | NULL out + clear polylines + null Cloudinary URL; stamp `gps_anonymized_at` | Saskatchewan Transportation Act ceiling on GPS pickup/dropoff retention. `route_snapshot_url` is a rendered map PNG — visual GPS PII on the same row. |
| `rides` row (full) | 7 years | Hard DELETE | Saskatchewan Transportation Act ceiling on trip records (also financial/tax) |
| `driver_location_history` | 90 days | Hard DELETE | Per-second GPS pings — pure PII surface beyond the chargeback window |
| `ride_messages` | 90 days | Hard DELETE | In-app chat — covers chargeback + dispute lifecycle |
| `refresh_tokens` | `expires_at` + 30 days | Hard DELETE | Tokens are already invalid at expiry; 30d grace lets `/auth/logout-all` forensics inspect recently-revoked sessions (B-P1-13) |
| `stripe_events` | 90 days | Hard DELETE | Stripe replays only within 30 days; 90d gives margin for late-investigated payment disputes |
| `audit_logs` | 7 years | Hard DELETE | Saskatchewan Transportation Act ceiling on action history; PIPEDA "no longer needed" applies after the regulatory window. Append-only between then; UPDATE blocked by trigger. |

**Out of scope for this job** (handled elsewhere or follow-ups):
- `disputes` — retain 7y by policy; separate migration
- `saved_addresses`, `emergency_contacts` — cascaded with user soft-delete

Changing any of the windows above is a **compliance event**, not a code
tweak. Process:
1. Open a ticket referencing the regulatory clause that justifies the change.
2. Get sign-off from legal + the founder.
3. Author a new migration with `CREATE OR REPLACE FUNCTION purge_pii_retention(...)` updating the `c_*_age` constants.
4. Update the table above in the same PR.
5. Never edit migration 50 in place — append-only convention.

---

## How It Runs

- **Trigger:** every backend replica spawns `retention_purge_loop` from
  `core/lifespan.py` at startup.
- **First execution:** sleeps until the next 03:00 UTC, then runs every
  24h thereafter.
- **Cross-replica safety:** before invoking the SQL function, the loop
  attempts `SET NX EX spinr:retention:purge:lock <pod_id> 82800` (23h).
  Whichever replica wins the SET NX runs the purge; the others log
  "another replica holds the lock, skipping" and wait for the next tick.
- **Audit trail:** the SQL function inserts one row into `audit_logs`
  per real (non-dry-run) execution with the JSONB result. Query the
  table to confirm a run happened and see what it deleted.
- **Failure handling:** any exception surfaces as `logger.exception` —
  no silent swallowing. The Postgres function never returns partial
  state: if any DELETE/UPDATE fails, the whole transaction rolls back
  and the next day's run will re-attempt the same windows.

---

## Manual Operations

### Confirm yesterday's run happened

```sql
SELECT created_at, details
FROM audit_logs
WHERE action = 'pii_retention_purge'
ORDER BY created_at DESC
LIMIT 5;
```

The latest row's `created_at` should be within the last ~25 hours. If
the most recent run is > 36 hours old, investigate:
- All replicas crashed before reaching 03:00 UTC?
- Redis leader lock got stuck (TTL is 23h — should have expired)?
- The SQL function is failing (look at backend logs for
  "retention_purge: rpc(purge_pii_retention) failed")?

### Dry-run (preview only — no rows mutated)

From `psql` with the service-role connection:

```sql
SELECT purge_pii_retention(p_dry_run => true);
```

Returns a JSONB object showing how many rows *would* be touched. No
inserts, deletes, or updates occur. Use this before bumping a retention
window in production to size the impact.

### Force a real run now

If a regulator request needs the retention purge applied immediately
(rather than waiting for 03:00 UTC), call from `psql` with the
service-role connection:

```sql
SELECT purge_pii_retention(p_dry_run => false);
```

This bypasses the Redis leader lock — only one operator should run it
at a time. The function is idempotent; two parallel calls would each
see the candidate set and one would block on row locks until the other
finishes, but neither would corrupt data.

### Suspend the purge temporarily

If you need to pause retention enforcement (e.g. evidence preservation
for a legal hold), the cleanest option is to revoke the function's
execute grant rather than touching application code:

```sql
REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM service_role;
```

The next loop tick will fail with a permissions error and log it; no
data is mutated. Restore with:

```sql
GRANT EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role;
```

Document any suspension in `docs/audit/legal-holds.md` with a start
date and the reason. Always pair the revoke with an end-date in the
calendar so the suspension does not silently outlive its purpose.

---

## Recovery: Mistaken Mass Deletion

Symptom: `(audit_logs.details::jsonb)->>'rides_deleted'` is unexpectedly
large (thousands of rows in a single run). The cast is required because
production stores `details` as TEXT — the function writes
`v_result::text` so the column always contains valid JSON.

Possible causes:
1. The `c_ride_keep_age` constant was lowered without a migration
   review (someone edited live).
2. Clock skew on the DB server — `now()` jumped forward.
3. The `rides.created_at` column was backfilled incorrectly during
   a data migration, making old rides look older than they are.

Recovery:
1. **Stop further runs immediately** by revoking execute (see above).
2. Open the most recent base backup or PITR window in Supabase.
   The `purge_pii_retention()` function deletes rows in a single
   transaction; a PITR to just before that transaction recovers all
   lost rows. See `docs/runbooks/pitr-restore.md`.
3. Investigate root cause before re-granting execute. If the issue
   was a constant change, revert it with a new migration that calls
   `CREATE OR REPLACE` to restore the policy values from this runbook.

---

## Notes

- The function is `SECURITY DEFINER` with `SET search_path = public, pg_temp`.
  This satisfies CLAUDE.md's "money-touching functions must pin
  search_path" rule (the same logic applies to PII destruction —
  destruction by a search-path-confused function would be catastrophic).
- The Redis leader lock is **belt-and-braces**, not a correctness
  requirement. Even if Redis is down and every replica runs the SQL
  function on the same day, the result is still correct — just noisier
  in `audit_logs`.
- The `gps_anonymized_at` column is **append-only**. Never UPDATE it
  back to NULL; doing so would re-anonymize an already-anonymized row
  on the next tick (harmless but pointless audit-log noise).
- This runbook does **not** cover regulator data export requests
  (PIPEDA s. 8). Those follow the user-rights flow in
  `docs/runbooks/security-incident.md` and `CLAUDE.md` § Compliance.
- **Schema drift is the historical failure mode of this job.** The whole
  purge is one transaction, so a single step keyed on a column the live
  table does not have aborts and rolls back *every* step — the loop logs
  one DB error a day and enforces nothing. It has happened three times:
  `driver_location_history.recorded_at` (fixed by migration 187),
  `stripe_events.created_at` (introduced by migration 67, fixed by 256),
  and `ride_messages.created_at` (fixed by 256). Steps D and F now resolve
  their column from `pg_catalog` at run time and `RAISE` if none of the
  candidates exist, and every run records what it resolved to under
  `retention_ts_columns` in the result JSONB — visible in the daily
  `pii_retention_purge` audit row and the loop's INFO log:

  ```
  retention_purge complete dry_run=False ... ts_cols={'ride_messages': 'timestamp', 'stripe_events': 'received_at'}
  ```

  If a re-fork of `purge_pii_retention` adds a step, check the column
  against the *live* table, not against a `CREATE TABLE IF NOT EXISTS` in
  `backend/migrations/` — several of those never applied to production.
  `backend/tests/test_retention_purge_column_drift.py` enforces this
  statically for every hard-coded cutoff.

---

## audit_logs append-only contract (B-P1-7)

Migration 51 locked `audit_logs` down to enforce its forensic role:

- **RLS**: SELECT-only for admins (gated by `users.role IN ('admin',
  'super_admin')`); service-role bypass for the backend's INSERT/DELETE.
- **PostgREST**: anon has no grant; authenticated has SELECT only.
- **UPDATE**: blocked unconditionally by trigger `audit_logs_no_update`,
  including for service_role. A backend bug that issues
  `update_one("audit_logs", ...)` raises `check_violation` instead of
  silently rewriting forensic history.
- **DELETE**: allowed only via the 7y retention step in
  `purge_pii_retention()`. No application code path DELETEs audit rows.

Migration 51 also fixed a latent bug in migration 50: the function's
own audit-log INSERT used column names from migration 08's
never-applied schema (`actor_id`, `actor_role`, `resource`). The fix
maps them to the production schema:

| Original (broken) | Production column | Value the function writes |
|---|---|---|
| `actor_id = 'system:retention_purge'` | `user_email` | `'system:retention_purge'` |
| `actor_role = 'system'` | (dropped — recorded in `details` JSON) | — |
| `action = 'pii_retention_purge'` | `action` (unchanged) | `'pii_retention_purge'` |
| `resource = 'system'` | `entity_type` | `'system'` |
| (missing) | `entity_id` | `v_started_at::text` (per-run identifier) |
| `details` JSONB | `details` TEXT | `v_result::text` |
| (missing) | `id` | `gen_random_uuid()::text` |

If a future migration moves `details` to JSONB on the live table,
update the cast in the same migration.

---

## Known Follow-Ups

- **Cloudinary asset deletion at 3y.** The 3-year scrub nulls
  `rides.route_snapshot_url` in the DB but does not delete the public
  Cloudinary PNG. The asset is unguessable (signed URL with content
  hash) so the residual exposure is low, but PIPEDA "no longer needed"
  argues for active deletion. Tracked as a follow-up: a weekly admin
  job that reads `rides.route_snapshot_url` for rows with
  `gps_anonymized_at < now() - INTERVAL '7 days'` and calls Cloudinary
  destroy on each public_id. Out of scope for B-P1-6.
- **`disputes` retention.** 7-year policy, separate migration.
