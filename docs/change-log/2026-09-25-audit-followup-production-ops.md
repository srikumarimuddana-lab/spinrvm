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
