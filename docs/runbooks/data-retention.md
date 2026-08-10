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
| DSAR-deleted accounts (`users`, cascaded `drivers`/`saved_addresses`/`support_tickets`) | 7 years from `deletion_scheduled_at` | Hard DELETE (Step H, migration 216, made operative by 289) | PIPEDA right-to-delete, gated by the 7y regulatory floor — only accounts with no `rides`, `driver_insurance_periods`, `payouts`, or `bank_accounts` rows are eligible; everything else stays retained by its own window. |
| `ride_routes` GPS geometry (`phase_polylines`, `road_polyline`, `road_polyline_pickup`) | 3 years | NULL/empty out (Step I, migrations 117/129) | Same Saskatchewan Transportation Act GPS ceiling as Step A, applied to the separate route-quality table |
| `ai_messages` / `ai_conversations` | 90 days | Hard DELETE (Step J, migration 141) | In-app AI assistant chat — same chargeback/dispute-lifecycle window as `ride_messages` |
| `surge_pricing` history | 90 days | Hard DELETE (Step K, migration 143) | Pricing-history PII surface beyond any dispute window |
| `price_searches` | 90 days anonymize `user_id`, 25 months hard delete | Anonymize then delete (Step L, migration 228) | Pre-booking fare estimates tied to a user; anonymized early since the estimate itself has analytics value, deleted later since PIPEDA "no longer needed" eventually applies to the anonymized rows too |
| `compliance_export_events` | 7 years | Hard DELETE, gated by session-flag (Step M, migration 285) | Same append-only/service-role-only delete pattern as `audit_logs` (Step G) — both are compliance audit trails with "only the retention purge may delete" |
| `users.first_name/last_name/email/profile_image` + `saved_addresses`, for `pending_deletion` accounts | 30 days from `deletion_requested_at` | NULL out profile fields, hard-delete `saved_addresses` (Step N, migration 296) | PIPEDA right-to-delete #1 (`regulatory-sk.md`) — "personal profile fields scrubbed within 30 days" of a deletion request. Independent of Step H's 7-year hard delete window; see ACTION_ITEMS.md B18. |

**Out of scope for this job** (handled elsewhere or follow-ups):
- `disputes` — retain 7y by policy; separate migration
- `saved_addresses`, `emergency_contacts` — cascaded with user soft-delete (also reachable via Step H for a DSAR-deleted account, and now via Step N's 30-day scrub for `saved_addresses` specifically)
- `driver_insurance_periods`, `driver_period_distances`, `stripe_disputes` — each has its own `NO ACTION` FK on `rides(id)`; Step B does not (yet) account for any of the three the way it now does for `financial_events` — see ACTION_ITEMS.md B23

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

## Append-only tables Step B's ride purge interacts with

Two tables retained independently of `rides` reference it by `ride_id`/`user_id`
and must survive a ride or account being purged out from under them:

- **`financial_events`** (the 7-year CRA/SOC2 money ledger, migration 58) —
  `ride_id` is `ON DELETE SET NULL` as of migration 294 (ACTION_ITEMS.md B17).
  Before 294, Step B's `DELETE FROM rides WHERE created_at < now() - 7y` had
  no exception handler and `financial_events.ride_id` had no `ON DELETE`
  action (default `NO ACTION`) — the first paid ride to cross 7 years would
  raise `foreign_key_violation` and abort the **entire** purge transaction,
  including Step A's GPS anonymization, repeating on every subsequent daily
  run. `ON DELETE SET NULL` (not `CASCADE`) was chosen deliberately: the
  transaction record itself must be retained for the full 7 years regardless
  of whether its ride has aged out — only the back-link to the (now-deleted)
  ride is lost.
  **Erratum (migration 295):** 294 alone did not actually fix this. Postgres
  implements `ON DELETE SET NULL` by issuing an internal `UPDATE` against the
  referencing table that goes through the normal trigger machinery — and
  `financial_events_no_mutate` (the table's append-only trigger, migration
  58/289) unconditionally raises on any `UPDATE` it doesn't recognize. So the
  SET NULL action itself would fail, and Step B would still abort — just with
  a trigger-raised error instead of a raw `foreign_key_violation`. 295 fixes
  this by extending `_financial_events_immutable()` to permit exactly one
  UPDATE shape unconditionally (no GUC — the FK action fires internally, with
  no chance for application code to set a session GUC first): nulling
  `ride_id` with every other column pinned unchanged. The column-pinning
  itself is the safety boundary.
- **`financial_events`** is also involved in **Step H** (DSAR hard-delete,
  below) via a different mechanism: a transaction-local GUC
  (`spinr.financial_events.allow_delete`), not an `ON DELETE` action, because
  Step H deletes the `financial_events` rows themselves (for a DSAR-deleted
  account whose full 7-year footprint has cleared), not just a `rides` row
  they point at. The table's `financial_events_no_mutate` trigger blocks
  UPDATE unconditionally and blocks DELETE unless that GUC is `'true'` —
  set immediately before Step H's DELETE and cleared immediately after,
  including on the error path (migration 289).
- **`compliance_export_events`** (Step M) uses the identical GUC-gate pattern
  (`spinr.compliance_export_events.allow_delete`) for the same reason —
  both are append-only compliance/audit logs where "only the retention purge
  may delete" is enforced by a trigger, not by table permissions alone.

If you're adding a new append-only table that Step B, H, or M might need to
reach into, follow whichever pattern matches: `ON DELETE SET NULL`/`CASCADE`
on the FK if the table is a *sibling* record independent of the row being
purged (financial_events → rides), or the GUC-gate pattern if the retention
purge itself needs to delete rows *from* that append-only table (financial_events
→ Step H, compliance_export_events → Step M).

---

## Step N: 30-day profile-PII scrub (ACTION_ITEMS.md B18)

`.claude/context/regulatory-sk.md`'s Right-to-delete section promises
"personal profile fields (name, email, home address, payment methods) →
scrubbed within 30 days" of a deletion request. Nothing implemented this
promise until migration 296 — `delete_account_pipeda` (the DSAR `/account`
endpoint) only set `deletion_requested_at`/`deletion_scheduled_at`/`status`
and revoked tokens; profile fields and `saved_addresses` stayed fully live
and queryable for the entire 7-year window until Step H's hard delete.

Step N runs independently of Step H, 30 days after `deletion_requested_at`
(not `deletion_scheduled_at`, which is `deletion_requested_at + 7y` and is
Step H's own eligibility field — a different anchor for a different purpose):

- NULLs `users.first_name`, `last_name`, `email`, `profile_image` and stamps
  `profile_scrubbed_at` (append-only marker, mirrors `gps_anonymized_at`).
- Hard-deletes `saved_addresses` for the account.
- Deliberately does **not** touch `phone` — `delete_account_pipeda`'s own
  response promises "sign in again anytime to reactivate" (a phone-OTP
  login), and `regulatory-sk.md`'s own field list excludes phone too.
- Payment methods are not touched by this migration — Spinr holds no local
  payment-methods table (Stripe is the system of record); a Stripe-side
  detach/delete is a separate API call, not a DB purge step, and is tracked
  as a follow-up rather than assumed handled.
- Does **not** touch `rides` rows, which stay attributable (Step H's model)
  through the full 7-year window regardless of profile scrub status.

**Decision record for B18 (anonymize-vs-delete):** three governing docs
(`regulatory-sk.md`, `CLAUDE.md` §Compliance, this runbook) previously stated
records are "anonymized... not deleted" after the retention window, while
migration 216 (operative since 289) actually hard-deletes DSAR accounts with
no anonymization at 7 years (Step H). This runbook does not reverse that —
Step H's hard-delete model ships and stays as-is; un-shipping it needs real
legal/founder review given its scope (Step H's subtransaction logic, the
driver-footprint eligibility guard, and the reactivation promise all depend
on it). What Step N closes is the separate, unambiguous 30-day promise that
nothing implemented at all — additive to user privacy regardless of which
way the anonymize-vs-delete question is eventually decided.

**Known gap, not implemented:** `regulatory-sk.md` also promises "rider
identity linked to trip: 7 years (hashed after 2)" — a general rule for
every ride, not just DSAR-requested ones. Implementing this literally
(hashing/nulling `rides.rider_id` at 2 years) would break every active
rider's own trip-history screen and any admin/refund lookup by rider for a
ride older than 2 years — a live, real-user-facing regression that needs its
own product/legal scoping before any code change, not a narrow backend fix.
Left as an open, documented gap — see ACTION_ITEMS.md B18.

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
