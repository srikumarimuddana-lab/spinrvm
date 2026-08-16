# Legacy Ride Count Drop Investigation (224 → 186)

**Date:** 2026-08-16
**Trigger:** Dual-run cutover audit, Phase 0.4 — the legacy-imported ride count in production no longer matched the count recorded in `docs/audit/2026-08-13-migrated-data-visibility-audit.md` (A30 in `ACTION_ITEMS.md`).
**Method:** Live queries against Supabase production (`soavhtdhefowwvforzwb`), Supabase's Postgres statement logs (`query_logs`, `source=postgres_logs`), and `pg_stat_statements`.
**Auditor:** Claude Code.

---

## 1. The discrepancy

`docs/audit/2026-08-13-migrated-data-visibility-audit.md` recorded, from a live query on 2026-08-13:

```sql
select count(*) as legacy_ride_count, min(ride_completed_at), max(ride_completed_at),
       count(*) filter (where rider_id is null) as null_rider,
       count(*) filter (where driver_id is null) as null_driver
from rides
where legacy_import_metadata->>'source' = 'legacy_mongo_booking_import';
-- 224 rows
```

The same query, run live on 2026-08-16, returns **186 rows** — a 38-row drop.

## 2. Ruling out non-deletion explanations

| Check | Result |
|---|---|
| Query-shape difference (`!= '{}'` vs exact-source filter) | Same count both ways — 186 |
| Soft-delete (`deleted_at`, migration 33) | 0 of 186 remaining rows soft-deleted; missing rows aren't hidden, they're absent from the table entirely |
| Multiple import batches / re-import | Single batch (`20260729184745`) both before and after |
| Different Supabase project/branch | One project, zero branches |
| Application code deleting `rides` | Grepped the entire backend — no code path exists |
| App-level `audit_logs` deletion record | Zero rows, ever |

Postgres's own `pg_stat_user_tables` shows 1,961 lifetime `DELETE` operations against `rides` (`n_tup_del`) against 718 lifetime inserts — confirming real delete activity has occurred against this table that the application never issued. (Note: this counter is cumulative since `stats_reset` on 2026-05-22, roughly 3 months of history, so this figure alone does not date to this specific incident — see §4.)

## 3. Why `postgres_logs` (Supabase's statement log) found nothing

Swept `postgres_logs` broadly across 2026-08-13 through 2026-08-16 (multiple 24h windows, the tool's per-call cap) for anything matching `rides`/`DELETE`/`TRUNCATE`. Found only DDL: `CREATE FUNCTION`, `ALTER TABLE`, `COMMENT ON`, `CREATE POLICY`, `CREATE TABLE` — all tagged `-- source: dashboard` (Supabase SQL editor) or `-- source: POST /mcp` (an MCP-connected tool session). **Zero plain `DELETE`/`UPDATE`/`INSERT` statements appeared anywhere**, despite `audit_logs` showing hundreds of real app-level ride writes (`ride_created`: 432, `ride_cancelled`: 150, `driver_rated`: 205) in overlapping windows.

Conclusion: this project's Postgres statement logging captures DDL only. Ordinary DML — from the app's own PostgREST traffic, a direct `psql`/service-role connection, or even the dashboard SQL editor — is structurally invisible to `postgres_logs` regardless of when it ran. This is a logging-configuration gap, not evidence that nothing happened.

## 4. Ruled out: FK cascade, pg_cron, and the retroactively-updated `purge_pii_retention()`

- No `ON DELETE CASCADE` foreign key exists from any table into `rides` (`pg_constraint` check) — cascade-delete is not the mechanism.
- `pg_cron` is not installed on this project (`cron.job` does not exist).
- `purge_pii_retention()` (migration 296, redefined via the dashboard on 2026-08-13 13:37 UTC) deletes rides older than 7 years — every legacy ride's `created_at` is in 2026, so this function, as currently defined, cannot be the cause. Zero `pii_retention_purge` audit rows exist, so it's unclear whether it has ever run for real in production at all.

## 5. What actually explains it — found via `pg_stat_statements`

`postgres_logs` couldn't help further, so this switched to `pg_stat_statements` — a separate, always-on catalog view that records normalized query shapes, call counts, and (critically) a `stats_since` timestamp per query, independent of log retention.

### 5a. A wholesale environment-reset script (ruled out for this specific gap)

Found a `DO $$ ... $$` block that unconditionally deletes `ride_child` tables, `driver_child` tables, non-admin `user_child` tables, then `DELETE FROM rides` / `DELETE FROM drivers` / `DELETE FROM users WHERE role <> 'admin'` — with no `WHERE` clause on the parent tables. It disables the append-only guard triggers on `driver_insurance_periods` and `financial_events` first. Called 3 times total; `stats_since` for this query is **2026-07-16** — before the legacy import (batch `20260729184745`, 2026-07-29) ever ran.

**Ruled out as the cause of the 224→186 gap**: a wholesale, unconditional `DELETE FROM rides` running after the 08-13 measurement would have left either 0 rows or a fresh batch tag on any re-imported rows. All 186 currently-present legacy rides carry the *original* `20260729184745` batch tag — they were never deleted and reinserted. Its `stats_since` predates the import entirely, consistent with a "clean the environment before the real import" step, not a later incident.

*(This script remains a serious standing risk independent of this investigation — see §7.)*

### 5b. A targeted, phone-scoped account-deletion script (the leading explanation)

Found a second `DO $$ ... $$` script, parameterized by `v_phones TEXT[]` and `p_dry_run BOOLEAN`. It resolves `users` by phone → `drivers` → **`rides` where `driver_id` or `rider_id` matches** → walks 16 groups of dependent tables → ends with:

```sql
DELETE FROM rides WHERE id = ANY(v_ride_ids);
DELETE FROM drivers WHERE id = ANY(v_driver_ids);
DELETE FROM users WHERE id = ANY(v_user_ids);
```

`pg_stat_statements` shows **two real (`p_dry_run := false`) executions**:

| `stats_since` (UTC) | Phones targeted | Mode |
|---|---|---|
| 2026-08-14 20:13:03 | `+13062929175` | real (`p_dry_run := false`) |
| 2026-08-14 20:38:25 | `+13066009097`, `+13065203307`, `+13065203304` | real (`p_dry_run := false`) |
| 2026-08-14 20:08:24 | `+13062929175` | dry run only |

Both real executions fall squarely inside the 2026-08-13 → 2026-08-16 gap window.

**Cross-checked against the legacy MongoDB export** (`bookings.csv`/`drivers.csv`/`customers.csv`, local 10-digit phone format): 3 of the 4 targeted numbers (`3062929175`, `3066009097`, `3065203304`) appear **repeatedly** as both driver and customer records — several explicitly test-labeled ("Test YK", "Yy", "Hh", "Test Y") alongside apparently-real names ("Kiran", "Tristan", "Yash Kumar", "Ryan D").

Since the legacy importer matches bookings to real Spinr accounts by phone (A30: 100% rider match, 94.2% driver match rate), any Spinr account behind these phone numbers that had a legacy-imported ride linked would have had that ride swept into `v_ride_ids` and deleted by this script — a coherent, well-evidenced mechanism for some or all of the 38-row gap.

## 6. What remains unconfirmed

- The exact 38 ride IDs affected — not recoverable; the deleted `users`/`drivers` rows are gone, and `RAISE NOTICE` output (which would have printed per-table deleted-row counts) is not captured at this project's configured log verbosity.
- Whether all 4 phone numbers' accounts actually had a legacy ride attached — the CSV cross-check is circumstantial (proves the phone numbers existed in the legacy data, not that the specific Spinr account row had a `rides` link at deletion time).
- Who ran these scripts, or from what connection — `pg_stat_statements` has no actor/session identity, unlike `postgres_logs`' `-- source:` tagging (which this script's execution never appeared in, consistent with §3's DDL-only logging).

## 7. Separate finding surfaced by this investigation — needs its own triage

Both scripts found in `pg_stat_statements` disable this repo's append-only regulatory guard triggers (`driver_insurance_periods_no_mutate`, `financial_events`'s delete gate, `audit_logs_no_delete`) in order to hard-delete `driver_insurance_periods` rows for the matched driver(s).

CLAUDE.md's own PIPEDA section states insurance-period transitions must be retained for the full 7-year regulatory window *regardless* of a deletion request — only PII fields are supposed to be scrubbed, not the record itself. A script that hard-deletes `driver_insurance_periods` as part of fulfilling a phone-scoped deletion appears to contradict that documented policy.

Also notable: `financial_events` is currently **0 rows** in production, despite 42 files in this repo actively reading or writing it (webhooks, reconciliation, ledger service, payment retry). This is consistent with one of these scripts having wiped it at some point, with nothing since repopulating it — worth an explicit check rather than assuming it's fine.

Neither of these was chased further in this investigation — flagged in `ACTION_ITEMS.md` A34 for follow-up, ideally by whoever owns these ad-hoc SQL scripts.

## 8. Bottom line

The 224 → 186 drop was very likely caused by a legitimate (or at least intentional) phone-scoped account-deletion script executed twice on 2026-08-14, whose logic explicitly resolves and deletes `rides` tied to the target accounts — not a bug, not silent data corruption, and not caused by anything in this repo's own import/dedup code. But this is the leading explanation based on strong circumstantial evidence, not a fully closed case — the exact affected rows and the human intent behind the two real executions are still unconfirmed. Every legacy-migration figure produced by this or prior audits should be treated as a snapshot until this is closed out with the people who ran these scripts.
