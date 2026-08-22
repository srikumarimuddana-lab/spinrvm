# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code session (see PR for session link) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety, admin |
| PR / commit link | #4374 |
| Related issue or gap ID | ACTION_ITEMS.md G2 |

## 1. Issue / gap identified

`schema_migrations` on the live Supabase project only had 331 rows tracked against 447
migration files merged to `main` — 116 files had never been run through
`run_migrations.py` or recorded as applied. Found while investigating why a just-merged
legal-docs migration wasn't showing content in the admin portal (a narrower instance of
the same pattern, fixed first in PR #4342).

## 2. Root cause

No single cause. Spot-checking each "missing" file's actual schema objects against the
live database showed most (~95 of 116) were already applied via some side-channel
(direct SQL editor, another agent session) that never wrote the `schema_migrations`
tracking row — a bookkeeping gap, not a real schema gap. A genuine minority (~17) had
real schema objects missing entirely, including safety/PII-critical ones (emergency
contact encryption, SOS contact suppression). One file (299) is an actual bug — it
declares a `UUID` foreign key against `users.id`, which is `TEXT` in this schema, so it
fails immediately if ever run as merged. A further 4 files are stale — they'd revert
already-fixed security/PII behavior back to a buggy prior state if ever run.

## 3. Fix / remediation

- Applied the ~17 genuinely-missing migrations directly against the live database
  (idempotent DDL: `CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`,
  `CREATE OR REPLACE FUNCTION`), verified via `information_schema`/`pg_catalog` before
  and after each, then recorded them in `schema_migrations` with their real file
  checksums so `run_migrations.py` recognizes them as applied.
- Backfilled `schema_migrations` tracking rows (checksum only, no schema change) for
  the ~95 already-applied-but-untracked files.
- Added migration `362_fix_rider_email_verification_otp_user_id_type.sql` as the
  fix-forward correction for 299's `UUID`/`TEXT` bug, per the append-only convention
  (299 itself is not edited).
- Left 4 migrations (`70`, `78`, `137`, `26`) deliberately unapplied and untracked —
  running them as merged would regress currently-correct security/PII behavior. Left
  migration `359`'s function-ownership step (`ALTER FUNCTION ... OWNER TO
  supabase_admin`) unapplied — blocked by insufficient privilege in this session
  (`must be able to SET ROLE "supabase_admin"`); the corrected function bodies from 359
  are applied, only the ownership transfer is outstanding.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same tables/functions:** the applied migrations touch
  `settings` (new default-off flag columns, read by `backend/core/*` and
  `routes/rides/safety.py`), `driver_daily_stats`/`driver_period_distances` (read by
  admin earnings/distance-log endpoints, additive columns with defaults — existing
  readers select named columns and are unaffected), `audit_logs` (RLS/trigger
  tightened — `INSERT` still goes through `service_role` unaffected; only
  authenticated-role `UPDATE`/`DELETE` and the old `FOR ALL` admin policy are removed),
  `emergency_contacts`/new `sos_contact_suppressions` table (new encryption RPC
  functions and a new opt-out table — no existing column type or read path changed),
  `corporate_accounts`/`users`/`rides` (two FK constraints added, guarded by a
  column-type check that skips the `ALTER` rather than erroring on mismatch), and
  several new indexes (`rides`, `disputes`, `promo_applications`, `payouts`,
  `lost_and_found`, `drivers`) which only add read-path performance, no write-path
  change.
- **Could this regress a flow that currently works?** No known regression — every
  applied statement is additive/idempotent and was checked against the live schema
  both before (confirm the target was actually missing) and after (confirm it applied
  cleanly) applying it. The one exception is the `audit_logs` RLS/grant tightening
  (migration 51), which *removes* `authenticated`-role `UPDATE`/`DELETE`/`INSERT` and
  the old `FOR ALL` admin policy — this is intentional (closing an append-only-log
  tamper gap) and does not affect the backend's own `service_role` write path.
- **Blast radius:** cross-cutting across the areas listed above, but every individual
  change within it is additive/idempotent DDL or a tracking-only bookkeeping insert —
  no data was deleted, no existing column type was changed except where explicitly
  corrected to match a documented bug (299/362), and the four flagged-harmful
  migrations were explicitly **not** applied.
- **Interaction with background loops / ride state machine / money:** none of the
  applied migrations touch ride-state columns, wallet/allowance deltas, or any of the
  18 background loops directly. `purge_pii_retention()` (migration 51) is one of the
  scheduled retention jobs — its body was corrected to fix a pre-existing
  schema-mismatch bug (it referenced columns from a never-applied migration-08 schema)
  and to add a new Step G (7-year `audit_logs` purge); it was not previously callable
  without erroring, so this is a net-new capability, not a change to working behavior.

## 5. User-experience effect

Nobody — this is backend-only schema/tracking reconciliation. The two new dark-launch
flags added (`rideless_sos_enabled`, `idle_location_v2_enabled`, and siblings) default
to `false`/off, matching their own migration headers' ship-dark intent; no rider,
driver, or admin sees any behavior change from this PR. No copy or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/362_fix_rider_email_verification_otp_user_id_type.sql` | New fix-forward migration correcting migration 299's `user_id UUID` bug to `TEXT` (matching `users.id`) | 299 fails with a `42804` type-mismatch error if ever run as merged; append-only rule means 299 itself can't be edited |
| `ACTION_ITEMS.md` | Added item G2 documenting the full audit, what was applied/tracked/left open | Durable record of a live-DB reconciliation that has no other single source of truth |
| `docs/change-log/2026-08-21-migration-tracking-drift-audit.md` | This file | Change Impact & Risk Log for the live-DB work described above |

Note: the majority of this change's actual impact (applying ~17 migrations and
backfilling ~95 tracking rows) happened directly against the live Supabase database via
MCP in this session, not as a file diff in this PR — there is no other migration file
that represents that work, since most of it was other migrations (already merged
separately) finally being applied, not new SQL.

## 7. Before / after

Not applicable in the code-diff sense — no existing function/endpoint behavior changed
in this PR's own file diff. The relevant "before/after" is: `schema_migrations` had 331
of 447 rows before this session's work; 441 of 447 after (the 6 remaining are the 4
do-not-run files plus migrations 299 and 359, both intentionally left untracked for the
reasons stated above).

## 8. Rollback plan

- **This PR's own file diff** (migration 362, ACTION_ITEMS.md, this log): standard
  `git revert` — migration 362's own header carries `DROP TABLE IF EXISTS
  public.rider_email_verification_otp;` as its rollback.
- **The live-DB changes this PR documents:** every individual migration applied in this
  session carries its own rollback SQL in its file header (e.g. `DROP COLUMN IF
  EXISTS`, `DROP TABLE IF EXISTS`, `DROP INDEX CONCURRENTLY IF EXISTS`) — additive
  schema changes, safe to roll back individually if any one of them is later found to
  cause a problem. The `schema_migrations` tracking-row backfill for already-applied
  files is pure bookkeeping and needs no rollback (removing a tracking row for an
  object that still exists would just make the audit re-flag it, not undo anything).
- A `git revert` of this PR alone does **not** undo the live-DB schema changes — those
  would need the individual rollback SQL from each affected migration's header, run
  manually against the Supabase project.

## 9. Verification performed

- [x] Blast-radius grep performed — every "missing" migration's target schema object
  (table/column/function/index/constraint) checked against `information_schema`/
  `pg_catalog` before applying, and re-verified after
- [x] Reviewed against relevant `CLAUDE.md` conventions — JWT trust model (caught `70`
  as a regression against it), PII/search_path pinning (caught `78` as a regression),
  append-only migrations (299 fixed forward, not edited), RLS-first for new tables
  (`sos_contact_suppressions`, `driver_insurance_period_corrections`,
  `rider_email_verification_otp` all ship with RLS enabled)
- [x] Every applied/tracked checksum re-verified against a fresh `sha256sum` of the
  actual file on disk after two transcription mistakes were caught mid-session (both
  self-corrected via `UPDATE` before being left in place)
- [ ] Automated tests run — not applicable, no application code path changed
- [ ] Manual repro steps followed in staging — this work was applied directly against
  the (single) live Supabase project; there is no separate staging environment for
  this repo per `CLAUDE.md`
- [x] Feature-flagged where user-visible and non-trivial — all new capability flags
  (`rideless_sos_enabled`, `idle_location_v2_enabled`, etc.) default off

## 10. Sign-off

- [x] Rollback plan is concrete and testable — per-migration rollback SQL exists in
  each file's own header
- [x] Blast radius is stated, not assumed — see section 4
- [x] No silent behavior change to an already-shipped flow — every new capability
  ships behind a default-off flag or is additive-only; the one non-additive change
  (`audit_logs` RLS/grant tightening) is documented explicitly in section 4
