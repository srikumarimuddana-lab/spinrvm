# 2026-09-25 — Production operations from the clean-sheet audit follow-up

*Performed by Claude Code through the Supabase connector (project `soavhtdhefowwvforzwb`, production). Each change was approved by the owner in this session. No code was deployed.*

## 1. Migration tracking back-fill (ROADMAP N25, LIVE-001)

| Field | Entry |
|---|---|
| **Issue/gap identified** | 39 migration files were applied to production but had no row in `schema_migrations`, so `run_migrations.py` would have tried to re-apply them, out of order. |
| **Root cause** | Schema changes reached production by hand, without the runner, so nothing recorded them (HIST-006, CARTO-005). |
| **Fix/remediation** | Inserted 39 rows (`filename`, `checksum` = SHA-256 of the file on `main` at `9e27915`, the same as the runner's `_checksum`). Each row has `applied_by = 'claude-audit-backfill-2026-09-25'`. Before inserting, every file's objects were confirmed to exist (`docs/audit/clean-sheet/10-live-checks.md` §6.1). |
| **Risk & impact on existing functionality** | Only `run_migrations.py` reads `schema_migrations`. **Risk 1:** a file whose objects exist but whose function bodies differ from the file would now never be re-applied. The object-level check does not diff bodies; this is stated as not verified. **Risk 2:** `applied_at` on these rows is the back-fill time, not the real apply time. |
| **User experience effect** | None. |
| **Files modified** | None in the repo; 39 rows in production `schema_migrations`. |
| **Rollback plan** | `DELETE FROM schema_migrations WHERE applied_by = 'claude-audit-backfill-2026-09-25';` |
| **Verification performed** | 39 rows returned by `INSERT … RETURNING`. Total tracked rows are now 558, which equals the 560 repo files, minus 3 `NEVER_APPLY` files, plus 1 tracked file with no repo source (LIVE-004). |
| **What was NOT verified** | Function-body equality between production and each file. `run_migrations.py --status` was not run (no `DATABASE_URL` in this sandbox). |

## 2. Applied migration 467 (`driver_always_location_gate_enabled`)

| Field | Entry |
|---|---|
| **Issue/gap identified** | Merged at 05:36 UTC today, but never applied. Saving the setting from the admin screen would fail. |
| **Fix/remediation** | Ran the file's statements verbatim: `ADD COLUMN IF NOT EXISTS … BOOLEAN NOT NULL DEFAULT FALSE` under `lock_timeout = '5s'`, plus its `COMMENT`. Recorded its tracking row with the runner checksum and `applied_by = 'claude-audit-apply-2026-09-25'`. |
| **Risk & impact** | Adding a column with a constant default is metadata-only in Postgres 17, so there is no table rewrite. The only reader is `backend/routes/drivers/profile.py:103`; `.get(...) is True` is unchanged because the column is false. |
| **User experience effect** | None. The flag is off. |
| **Rollback plan** | Per the file header: `ALTER TABLE public.settings DROP COLUMN driver_always_location_gate_enabled;` and delete its tracking row. |
| **Verification performed** | The new column reads `false`, and its tracking row exists. |
| **What was NOT verified** | The admin settings screen was not exercised. |

## 3. Stopped server-side Meta per-ride events (STRAT-004, escalation E-S3; owner decision)

| Field | Entry |
|---|---|
| **Issue/gap identified** | The backend sent per-ride purchase events to Meta. Meta is not disclosed as a sub-processor, which contradicts the "no ad SDKs" principle. |
| **Fix/remediation** | Set `settings.meta_rider_dataset_id` and `settings.meta_driver_dataset_id` to `''`. The access token was left stored. `backend/utils/meta_capi.py` skips every send when a dataset id is empty (`if not dataset_id or not access_token`). |
| **Alternative considered** | Clearing the access token would do the same. Rejected: it is a secret, and restoring it means generating a new one in Meta. The dataset ids are non-secret and are visible in Meta Events Manager. |
| **Risk & impact** | Readers: `meta_capi.py` only. Sends stop within the 60 s settings cache (`backend/settings_loader.py:19`). Ride, payment and sign-up flows are unaffected, because Meta calls return `False` and never raise. |
| **User experience effect** | None visible. |
| **Rollback plan** | Re-enter both dataset ids in the admin settings, or with an `UPDATE`. The values were captured before the change; they are held by the owner and shown in Meta Events Manager. They are not written here. |
| **Verification performed** | `UPDATE … RETURNING` shows both ids empty and the token still stored. |
| **What was NOT verified** | That events actually stopped at Meta; that needs Events Manager. **The mobile apps' own Meta SDK is not affected by this change** and needs an app release to remove or disable. |

## 4. Enforced incentive windows and budgets (ROADMAP N28)

| Field | Entry |
|---|---|
| **Issue/gap identified** | `start_date`, `end_date`, `conditions` and `max_budget` on `ride_incentives` were enforced by nothing. A campaign that ended or overspent would keep paying. |
| **Fix/remediation** | `settings.incentive_eligibility_enforced = true`. The enforcement code already shipped dark in `backend/services/incentive_service.py`. |
| **Risk & impact** | Pre-flip audit of live data: 3 active campaigns, 0 past their end date, 0 not yet started, 0 at or over budget, 0 with conditions. **No driver's current eligibility changes.** Future overruns are blocked. |
| **User experience effect** | None today. A driver may later see no bonus on a campaign that has ended or spent its budget, which is the intended behaviour. |
| **Rollback plan** | `UPDATE settings SET incentive_eligibility_enforced = false WHERE id = 'app_settings';` It takes effect within 60 s. Bonuses already paid are not affected either way. |
| **Verification performed** | `UPDATE … RETURNING` shows `true`; the campaign audit is above. |
| **What was NOT verified** | No live ride exercised the enforced path after the flip. |

## 5. Applied migration 473 (`instant_payout_daily_cap_cad`, merged in #5791)

| Field | Entry |
|---|---|
| **Issue/gap identified** | #5791 merged a new setting column that was not yet in production. The per-driver instant-payout daily cap could not be turned on. |
| **Fix/remediation** | Owner-approved. The file's DDL was run verbatim under `lock_timeout = '5s'`: `ADD COLUMN IF NOT EXISTS instant_payout_daily_cap_cad NUMERIC(10,2)` with its `> 0` CHECK, plus its `COMMENT`. The tracking row was inserted in the same transaction, with checksum `f992927d…` (runner `_checksum`) and `applied_by = 'claude-audit-apply-2026-09-25'`. |
| **Risk & impact** | The column is nullable with no default, so the change is metadata-only and there is no table rewrite. NULL means no cap, which is behaviour identical to before. The only reader is `request_instant_payout`. Admin settings saves use `exclude_none`, so they were unaffected before and after. |
| **User experience effect** | None until an admin sets a cap. |
| **Rollback plan** | Per the file header: `ALTER TABLE settings DROP COLUMN IF EXISTS instant_payout_daily_cap_cad;` and delete its tracking row. The reader treats a missing key as NULL. |
| **Verification performed** | Before: the column was absent and the row untracked (558 tracked rows). After: `settings.instant_payout_daily_cap_cad` reads `null` and the tracking row is present. |
| **What was NOT verified** | Setting a cap from the admin screen. |

## 6. Migration 474 (`function_search_path_hardening`, merged in #5794): rolled-back test, then applied

| Field | Entry |
|---|---|
| **Issue/gap identified** | LIVE-002. The Supabase security linter flagged 16 `public` functions with a mutable `search_path`. Four of them depend on PostGIS, which lives in schema `extensions`. A wrong path would break `update_driver_location`. |
| **Why not a Supabase branch** | Supabase preview branches are rebuilt from Supabase's own migration history. That history holds only 46 recent migrations. The base schema, including the four PostGIS functions and the `drivers` table, was created by `run_migrations.py`. A branch would therefore not contain the objects under test. The owner approved a rolled-back production test instead. |
| **Rolled-back test** | A single `DO` block ran all 16 `ALTER FUNCTION … SET search_path` statements. It then called `get_service_area_for_point(52.1332, -106.67)`, `find_nearby_drivers(52.1332, -106.67, 5000)` and `update_driver_location('<non-existent id>', 52.1332, -106.67)`, which matches no row, so nothing was written. The block ended with `RAISE EXCEPTION`, which forces a rollback. Result: all three calls succeeded under the new path, so there was no `st_* does not exist` error, and the proconfig showed `search_path=public, extensions, pg_temp`. After the rollback, `update_driver_location.proconfig` was `null` again. The same calls without the change returned identical row counts (0 and 0). |
| **Fix/remediation** | Owner-approved. The merged file's statements ran verbatim: `BEGIN`, `lock_timeout 2s`, `statement_timeout 20s`, the pre-check DO block, the 16 ALTERs, the post-condition DO block, then `COMMIT`. The tracking row was inserted inside the same transaction, with checksum `3772b4d9…` (runner `_checksum`) and `applied_by = 'claude-audit-apply-2026-09-25'`. |
| **Risk & impact** | Function config only; bodies, SECURITY mode and grants are unchanged. The live callers are `update_driver_location` (driver location route and WebSocket) and `get_service_area_for_point` (promotions). A side effect is that the two SQL functions can no longer be inlined by the planner. This was not measured and is expected to be negligible. |
| **User experience effect** | None. |
| **Rollback plan** | For each of the 16 functions, `ALTER FUNCTION public.<fn>(<types>) RESET search_path;`. The full list is in the migration header. Delete the tracking row. This needs no deploy and takes seconds. |
| **Verification performed** | After apply: 16 of 16 functions pinned, the post-condition passed, the tracking row is present, the same three calls succeed with identical results, and `get_advisors(security)` no longer reports any `function_search_path_mutable` finding. The remaining security lints are the expected ones: `rls_enabled_no_policy` INFO on 61 tables, a consequence of the service-role access model; and the documented LIVE-003 exception on `is_party_to_lost_and_found_case`. |
| **What was NOT verified** | `match_and_claim_driver` was not called, because it has no production caller and calling it would claim a driver. The 10 trigger functions were not fired, because that needs writes. Their bodies use only `pg_catalog` built-ins. Latency after the change was not measured. |

## 7. Recorded migration 476 (`route_deviation_alert_enabled`, merged in #5786, LIVE-004)

| Field | Entry |
|---|---|
| **Issue/gap identified** | Production had the column `settings.route_deviation_alert_enabled`, and it was live (`true`), but no repo migration created it. #5786 added `476_route_deviation_alert_enabled_setting.sql`, which restores the missing source for it. |
| **Fix/remediation** | Owner-approved earlier in the session. The file's statements were run verbatim under `lock_timeout = '5s'`: `ADD COLUMN IF NOT EXISTS …` (a no-op, because the column exists) and its `COMMENT`. The tracking row was inserted in the same transaction, with checksum `bcebd9eb…` (runner `_checksum`) and `applied_by = 'claude-audit-apply-2026-09-25'`. |
| **Risk & impact** | None. `IF NOT EXISTS` skipped the add, so the live value was untouched. The only reader is `route_deviation_alerter._deviation_alert_enabled()`. |
| **User experience effect** | None. Route-deviation safety alerts stay **on**. |
| **Rollback plan** | Delete the tracking row only. **Never drop the column in production**, because it is the live kill switch. |
| **Verification performed** | Afterwards `route_deviation_alert_enabled` still reads `true`. There are 6 tracked `47x` rows, and 561 tracked rows in total. The orphaned `415_route_deviation_alert_enabled_setting.sql` row stays as history. |
| **What was NOT verified** | Nothing further was needed; the database change was a no-op. |
