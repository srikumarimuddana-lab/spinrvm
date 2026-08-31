# Migration drift reconciliation — `backend/migrations/` vs live `schema_migrations`

**Date:** 2026-08-31
**Scope:** ACTION_ITEMS.md C22, the deferred "broader `schema_migrations` reconciliation" — a full diff
of every repo migration file against the live tracking table, using path (a) from the item's own text:
"fixing `run_migrations.py`'s own dry-run path to talk to the live table and diffing its output against
the [] repo files." Built as a **new, standalone, read-only script**
(`backend/scripts/audit_migration_drift.py`) instead of editing `run_migrations.py` directly, because a
separate concurrently-running task in this repo is adding a skip-list feature to that exact file.

**Out of scope, by design (per C22's own text):** individually verifying each untracked file's actual
live-schema state. That is the "~2.5x this session's scope" manual file-by-file audit C22 explicitly
defers — not attempted here.

## How the data was obtained

Live access was available via the Supabase MCP `execute_sql` tool (project `soavhtdhefowwvforzwb`,
`spinrmobileapp`), used for a single read-only query:

```sql
SELECT filename, checksum, applied_at, applied_by FROM schema_migrations ORDER BY filename;
```

No `DATABASE_URL`/`TEST_DATABASE_URL` was configured in this session for a direct psycopg connection, so
the script's `--tracked-json` input path was used: the query result was saved to a local JSON file and fed
into `audit_migration_drift.py`, which ran the same diff logic it would run against a direct DB connection.
The script itself never wrote to `schema_migrations` and never executed migration SQL — confirmed by
inspection (the only `cur.execute` in the DB-connection code path is the single `SELECT` above; the
`--tracked-json` path used for this run performs no database I/O at all).

## Results

| Bucket | Count |
|---|---|
| Total repo `.sql` files (`backend/migrations/`) | **466** |
| Tracked, checksum matches | **456** |
| Tracked, checksum **MISMATCH** | **0** |
| **Untracked** (no `schema_migrations` row at all) | **10** |

For comparison, the reference point in ACTION_ITEMS.md C22 (2026-08-17/18 sessions) was **161/407**
tracked. Coverage has since risen substantially — most likely from a full backfill pass (`applied_by`
values on the live rows show a large `backfill-verified` batch dated 2026-08-14 plus a second batch dated
2026-08-21 with `applied_by='postgres'`) between that session and this one, not from this session's own
work. This session did not perform or request any such backfill.

**No checksum mismatches found.** Every one of the 456 tracked files' repo content hashes exactly to what
`schema_migrations` recorded — no evidence of the append-only rule being violated, and no evidence of a
checksum-computation bug between `run_migrations.py`'s `_checksum()` (SHA-256 of raw file bytes) and
whatever produced the live rows.

**Zero orphaned tracking rows**, checked as a supplementary sanity pass beyond the three requested buckets:
every one of the 456 `schema_migrations` rows corresponds to a real file still in `backend/migrations/`
(no row for a renamed-away or deleted file).

## Untracked files (the actionable list)

10 files have no `schema_migrations` row on production at all. This does **not** mean they haven't been
applied — as C22's own prior findings showed (migrations 286 and 297 were untracked but genuinely live), a
missing tracking row is a bookkeeping gap at least as often as a real application gap. Determining which is
which for each file below is exactly the deferred manual audit; this list only prioritizes it.

| File | Touches | Priority |
|---|---|---|
| `26_rls_coverage_gap.sql` | RLS | **High** — RLS policy gap by name; if genuinely unapplied, a real security gap |
| `70_fix_financial_events_rls.sql` | Money (`financial_events`) + RLS | **High** — money-table RLS fix; also the exact file `backend/tests/rls/conftest.py` applies verbatim in its own test fixture (as a *known-good* SQL body for tests), which is not evidence about production's live state either way |
| `78_fix_pii_function_search_path.sql` | PII / security (`search_path` pinning on a `SECURITY DEFINER` function) | **High** — unpinned `search_path` on a PII-touching function is a privilege-escalation vector if unapplied |
| `137_fix_pii_encrypt_pgsodium_perms.sql` | PII / encryption permissions | **High** — permission fix on PII encryption path |
| `299_rider_email_verification_otp.sql` | Auth (OTP) | **Medium-High** — auth-adjacent; worth confirming before assuming rider email OTP verification works as coded |
| `376_corporate_wallet_adjust_idempotency.sql` | Money (corporate wallet idempotency) | **High** — money idempotency guard; if unapplied, a double-adjustment risk exists on the corporate wallet path |
| `373_saved_addresses_legacy_import_metadata.sql` | Non-critical (metadata column) | Low |
| `375_incentive_eligibility_enforcement_flag.sql` | Feature flag column | Low |
| `376_service_area_tax_history.sql` | Reference/history table | Low |
| `377_zoho_desk_tickets_sla_breach_alerted.sql` | Support-ticket integration | Low |

Five of the ten (`26`, `70`, `78`, `137`, `376_corporate_wallet_adjust_idempotency`) touch money, auth, or
RLS by filename and are the priority follow-up set. Per this task's explicit scope boundary, none of the
ten were individually cross-checked against the live schema in this session — that is the deferred,
higher-stakes manual audit.

## What this closes vs. what's still open

**Closes:** C22's "Still open" line item (a) — `run_migrations.py`'s dry-run path talking to the live table
and diffing against the repo file list — now exists as a reusable, read-only tool
(`backend/scripts/audit_migration_drift.py`) and has been run against production once, producing the
numbers above.

**Still open:**
- The 10 untracked files above still need individual live-schema verification (path (b) from C22, or
  `verify_applied_migrations.py`'s schema-introspection approach) to determine bookkeeping-gap vs.
  real-application-gap for each — not attempted here, consistent with C22's explicit scope boundary.
- No live `DATABASE_URL`/`TEST_DATABASE_URL` was available in this session for the script's default
  DB-connection path; it was only exercised via `--tracked-json`. The DB-connection code path itself
  (`_fetch_tracked_from_db()`) is untested against a real connection in this session — only its
  `--tracked-json` sibling input path was exercised end-to-end. Unit tests cover the diff logic
  (`build_report()`) directly regardless of input source.

## Files

- `backend/scripts/audit_migration_drift.py` — the new read-only reconciliation script.
- `backend/tests/test_audit_migration_drift.py` — unit tests for its three-bucket diff logic (mocked
  inputs, no live DB dependency).
- This report.
