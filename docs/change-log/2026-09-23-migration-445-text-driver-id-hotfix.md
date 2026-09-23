# 2026-09-23 — Hotfix: uuid-typed ride/driver ids in migrations 444/445 (#5717 follow-up)

| Field | Detail |
|---|---|
| **Issue/gap identified** | #5717 merged migrations that type TEXT ids as uuid. In 445, `update_live_driver_marker(p_driver_id uuid, …)` was live in production, and every driver live-location write (REST v1/v2, WebSocket) now goes through it. In 444, `ride_payment_operations.ride_id uuid REFERENCES rides(id)` cannot be created on a fresh database. |
| **Root cause** | `rides.id` and `drivers.id` are TEXT. PL/pgSQL does not type-check function bodies at CREATE time, so 445 applied cleanly and would have failed at first call (`operator does not exist: text = uuid`). The PR's SQL tests used a minimal schema with uuid ids, and the unit tests mocked `supabase.rpc`. |
| **Fix/remediation** | (1) **Production hotfix, 2026-09-23 ~01:00 UTC (user-approved):** in one transaction, dropped the uuid overload and installed the identical function body with `p_driver_id text`; granted EXECUTE to service_role only. Verified: only the text signature exists, and a call with a nonexistent id returns `false` without error. (2) **Repo:** 444 and 445 added to `NEVER_APPLY` (append-only rule). New `447_fix_payment_ops_and_live_marker_text_ids.sql` carries the corrected, idempotent definitions of both, plus a guard test that no migration ≥447 types a ride/driver id as uuid. |
| **Risk & impact on existing functionality** | Callers: `driver_repo.update_driver_location` only. Its callers are `routes/drivers/location.py` (v1, v2 trip, v2 idle, location-live) and `routes/websocket.py` (single and batch). The function body is unchanged, so accept/reject behaviour is unchanged. On production, 447 is a no-op: the table already exists with `ride_id text`, and the function was already replaced. Skip-listing 444/445 means a fresh environment builds these objects from 447 instead. |
| **User experience effect** | Drivers: live location updates work. Before the hotfix, any driver going online would have had their WebSocket closed and REST location posts 500. No drivers were online between the #5717 deploy and the hotfix (`drivers.is_online` count 0; last driver update 18:52 UTC). |
| **Before/after** | `p_driver_id uuid` → `p_driver_id text`; `DROP FUNCTION IF EXISTS …(uuid,timestamptz,jsonb)` added. |
| **Rollback plan** | None needed for the function: the uuid overload can never succeed. Reverting this PR only restores the skip-list gap; it does not touch production. |
| **Verification performed** | Production: catalog check plus a live call (see above). Repo: `test_migration_447_text_ids.py`, `test_run_migrations_skip_list.py` and `test_migration_444_payment_operations.py` (10 passed). The guard regex matches the original 444 and 445. Ruff clean. |
| **What was NOT verified** | 447 was not executed against a fresh Postgres; it is a copy of already-applied production DDL, and every statement is idempotent. There is no end-to-end driver-app location test against production, because no driver was online. |

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/migrations/447_fix_payment_ops_and_live_marker_text_ids.sql` | New fix-forward migration | Corrected text-typed definitions |
| `backend/scripts/run_migrations.py` | `NEVER_APPLY` entries for 444, 445 | Append-only; never re-run broken files |
| `backend/tests/test_migration_447_text_ids.py` | New guard tests | Prevent uuid-typed ids recurring |
