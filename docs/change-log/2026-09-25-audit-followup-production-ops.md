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

## 8. Applied migrations 479 and 477 (admin money caps and disputes columns, merged in #5802)

| Field | Entry |
|---|---|
| **Issue/gap identified** | #5802 merged at 15:54 UTC. Railway auto-deployed it from `main`: deployment `969d5917`, live about 15:56. Its two migrations were not yet in production. Until they were applied, every admin settings save would fail with PGRST204, because the dashboard round-trips the new fields. Dispute create and resolve would also fail. |
| **Fix/remediation** | Owner-approved at about 15:57. Both merged files were run verbatim, each in its own transaction with `lock_timeout 2s` and `statement_timeout 20s`, and each tracking row was inserted inside the same transaction. Checksums (from the runner's `_checksum`): 479 `aeb4fd22…`, 477 `980e928f…`. `applied_by = 'claude-audit-apply-2026-09-25'`. |
| **Risk & impact** | Additive only. The new columns are nullable, or `NOT NULL DEFAULT FALSE`, which is a metadata-only change with no rewrite. `disputes` has 0 rows. |
| **User experience effect** | None. The cap and the alert threshold are NULL (disabled), and `admin_dispute_refunds_enabled` is `false`. |
| **Rollback plan** | Behavioural rollback is already in place, because everything is off. For the schema, follow each file's header: drop the added columns only after reverting the code, and delete the tracking rows. |
| **Verification performed** | Before the apply, neither set of columns existed. Afterwards, the settings cap and threshold read `null`, the refund flag reads `false`, all 4 `disputes` columns exist, and both tracking rows are present. Railway deploy logs from 15:54 UTC, filtered for `PGRST204` / `admin_money` / `admin_dispute_refunds` / `disputes`, returned no entries, so no failed saves were seen in the ~2-minute gap. |
| **What was NOT verified** | An admin settings save and a dispute resolve were not run through the UI after the apply. |

## 9. Instant payouts switched off in every service area (owner decision: weekly payouts only)

| Field | Entry |
|---|---|
| **Issue/gap identified** | The owner decided (2026-09-25) that Spinr pays drivers weekly only, with no instant payout option. Production still had `service_areas.instant_payout_enabled = true` in all 6 areas, and `settings.instant_payout_daily_cap_cad` was NULL, so no cap applied. `POST /api/drivers/payouts/instant` was therefore reachable by any driver with Stripe Connect and a balance, even though no app UI calls it. |
| **Fix/remediation** | Owner-approved at about 17:00 UTC. Ran `UPDATE service_areas SET instant_payout_enabled = false WHERE instant_payout_enabled IS TRUE`, which updated 6 rows. The code removal follows in branch `claude/remove-instant-payouts` (migration 483 makes the column default `false`). |
| **Risk & impact** | The gate (`_instant_payout_area_verdict`) re-reads the row on every request, so the change took effect immediately and needed no deploy. `GET /drivers/balance` now reports `instant_payout_available: false`. The weekly auto payout (`utils/auto_payout.py`) does not read this flag. |
| **User experience effect** | None visible. No driver-app UI exists, and 0 instant payouts have ever been executed in production. |
| **Rollback plan** | `UPDATE service_areas SET instant_payout_enabled = true;` (no deploy). This contradicts the owner's decision, so it is for emergency use only. |
| **Verification performed** | Before: 6 of 6 areas were `true`, and there were 0 `payouts` rows with `payout_type = 'instant'`. After: 0 areas have `instant_payout_enabled IS NOT FALSE`. |
| **What was NOT verified** | A driver's actual `POST /payouts/instant` call was not made after the change; the gate logic was read in code only. New service areas still default to `true` until migration 483 ships. |

## 10. Reset the C136 incident driver's stale destination

| Field | Entry |
|---|---|
| **Issue/gap identified** | The driver in the C136 incident (driver id prefix `d491bb3c`) still had `destination_mode = true`, with destination coordinates set and `destination_set_at` / `destination_expires_at` NULL. Dispatch has ignored the stale row since C136, because a NULL expiry counts as off. However, the driver-app Destination Mode screen still showed "on". On 2026-09-24 this state caused 0 offers: recomputing the 5% rule for the 8 rides created 16:30–16:50 UTC gave driver-to-destination 4.33 km, the nearest drop-off-to-destination 4.76 km, and 0 of 8 rides passing. |
| **Fix/remediation** | Owner-approved at about 17:00 UTC. For that one driver row only (matched by user and id prefix), set `destination_mode = false` and set `destination_address`, `destination_lat`, `destination_lng`, `destination_set_at` and `destination_expires_at` to NULL. |
| **Risk & impact** | Single row. Only the dispatch destination filter and the destination endpoints read these columns. |
| **User experience effect** | The driver's Destination Mode screen now shows off. Their dispatch eligibility is unchanged, because the row was already ignored. |
| **Rollback plan** | None needed. The previous destination was deliberately not copied into this log, because it is an exact address (PIPEDA). If the driver wants it, they can set it again. |
| **Verification performed** | After the change, 0 drivers in production have `destination_mode` true or `destination_lat` set. |
| **What was NOT verified** | A live offer to this driver was not observed. The owner should retest with a Regina XL booking while the driver stays online for more than 2 minutes. Fly.io (the primary host) logs and deploy state were not inspected. |

## 11. Applied migration 481 (`corporate_kyb_refuses_closed_company`, merged in #5805)

| Field | Entry |
|---|---|
| **Issue/gap identified** | #5805 merged at about 17:09 UTC. Until its kill-switch column exists, the code treats the flag as ON, but admins cannot see or change it in Settings. |
| **Fix/remediation** | Owner-approved at about 17:12 UTC. Ran the merged file's statements verbatim (`ADD COLUMN IF NOT EXISTS … BOOLEAN NOT NULL DEFAULT TRUE`, plus its `COMMENT`) in one transaction, with the tracking row inserted: checksum `ef545ac6…` (runner `_checksum`), `applied_by = 'claude-audit-apply-2026-09-25'`. |
| **Risk & impact** | Additive. It adds a metadata-only default on the one-row `settings` table, and behaviour is unchanged. |
| **User experience effect** | None. The KYB guard was already active through the code default. |
| **Rollback plan** | Behaviour: `UPDATE settings SET corporate_kyb_refuses_closed_company = false WHERE id = 'app_settings';`. Schema: drop the column after reverting the code, then delete the tracking row. |
| **Verification performed** | The flag reads `true`, and the tracking row checksum matches the file on `main`. |
| **What was NOT verified** | An admin settings save round-trip with the new field was not run through the UI. |
