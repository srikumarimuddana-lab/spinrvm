# Legacy Backfill Scripts — Rollout Runbook

**Status:** written 2026-08-20, ahead of any `--apply` run. Both scripts covered here have been
built, tested against a local fake Supabase client, and reviewed — but **never run with `--apply`
against any environment** (mocked, staging, or production). This document describes the safe
procedure for whoever eventually runs them. **It is not, itself, the sign-off to run them** — see
"Sign-off" at the bottom.

Related reading:
- `docs/change-log/2026-08-19-legacy-duration-estimated-backfill.md` — build history for the
  duration-estimated backfill, including the concurrent-writer risk this runbook accounts for.
- `docs/change-log/2026-08-19-legacy-backfill-concurrent-writer-fix.md` — the code-level fix for
  that risk (this session).
- `docs/runbooks/legacy-migration-playbook.md` — the broader Oct 30 final-cutover playbook (not
  modified by this document; read it for the full migration timeline these two scripts sit inside).

## The two scripts

### 1. `backend/scripts/backfill_legacy_driver_sin_dob.py`

Backfills `drivers.sin` (vault-encrypted) and `drivers.date_of_birth` (plain) for drivers already
imported by the one-time Saskatoon driver CSV import (`legacy_saskatoon_driver_import` in
`legacy_import_metadata.source`). Source data is `banks.csv` and `drivers.csv` from the raw MongoDB
export of the old app — `banks.csv` carries SIN/DOB keyed by a Mongo ObjectId, `drivers.csv` resolves
that ObjectId to a phone number, and the phone number matches against Spinr's own `drivers` table.
Only touches drivers already tagged with the importer's own source key (a phone-number coincidence
can never reach an organic driver's SIN/DOB), and never overwrites an existing `sin` or
`date_of_birth` — self-entered data always wins over the legacy import. Write path
(`apply_legacy_sin_dob_import`) uses a write-time `.is_(col, "null")` guard per column, re-checked
immediately before each write. **Status: built, unit-tested, never run with `--apply`.**

### 2. `backend/scripts/backfill_legacy_ride_duration_estimated.py`

Backfills a `duration_estimated: true/false` marker into `rides.legacy_import_metadata` for rides
already committed by the legacy booking importer (`legacy_mongo_booking_import` source), based on
whether the row has `ride_started_at` (measured) or not (estimated from distance/average speed at
import time). The booking importer itself was fixed on 2026-08-19 to stamp this marker on every
*future* import, but that fix is import-code-path-only — it does nothing for the ~186 rides the
2026-07-29 production import already committed before the fix existed. This script is the deferred
follow-up that closes that gap for already-committed rows, without touching `duration_minutes`
itself or re-estimating anything. Write path (`apply_duration_estimated_backfill`) re-reads each
row's current metadata immediately before writing and refuses to touch a row that already carries
the marker (from the importer itself or an earlier run of this script). As of this session it also
carries a second, whole-column optimistic-concurrency guard — see next section.
**Status: built, unit-tested, never run with `--apply`.**

## The concurrent-writer risk, and how it's addressed

Both scripts write into JSONB columns via a read-current-row → merge-a-key-in-locally →
write-the-whole-column-back pattern. A **third**, separate, pre-existing manual backfill,
`backend/services/legacy_gst_backfill_service.py`, does the same thing against the exact same
column the duration-estimated backfill uses: `rides.legacy_import_metadata` (it adds
`old_payout_gst_amount`). If two independent scripts read-merge-write the same JSONB blob close
together, each one's write can silently drop a key the other just added — a classic lost-update
race.

**Current actual risk level: low, but not zero going forward.** `legacy_gst_backfill_service.py`
has **no commit/apply function at all today** (confirmed by reading the file in full this session —
it is a dry-run plan-and-report tool only, by explicit design: "Inserting the actual UPDATE is a
separate, later step"). So there is no live code path today that could actually race against the
duration-estimated backfill's writes. The risk is real for the day someone *does* add a commit path
to the GST backfill.

**What this session's fix does:** `apply_duration_estimated_backfill` now carries a second,
whole-column snapshot-equality guard in addition to its existing per-key guard — the update only
succeeds if `legacy_import_metadata` is still exactly what this function read moments earlier. Any
concurrent writer to that row (this script racing itself, or a future GST-backfill apply path) is
now caught as a reported conflict instead of a silent data loss, and is safe to retry on the next
run. `legacy_gst_backfill_service.py`'s module docstring now documents that its future commit path
must use the same guard pattern. See
`docs/change-log/2026-08-19-legacy-backfill-concurrent-writer-fix.md` for the full before/after and
why a Postgres advisory lock was considered and rejected (these scripts only have the
`supabase-py`/PostgREST client, no raw psycopg connection, and a session-level advisory lock can't
reliably span PostgREST's per-request connection pooling without a new migration-defined RPC
function, which was out of scope for this fix).

**Operationally, regardless of the code-level guard: do not run the duration-estimated backfill and
a (future) GST backfill `--apply` concurrently on purpose.** The guard converts a race into a safe,
reportable conflict, not a fast or efficient way to run two migrations — if both scripts are
`--apply`'d against overlapping rows around the same time, expect some fraction of updates to come
back as conflicts requiring a re-run, not a clean single pass. Run one to completion, verify its
report, then run the other.

## Recommended run order / sequencing (a recommendation — confirm with the product owner)

- **SIN/DOB backfill (drivers) has no dependency on the Oct 30 final MongoDB cutover.** It targets
  drivers from the already-completed, one-time Saskatoon driver CSV import — a population that
  doesn't change based on when the *booking* data gets its final cutover. There is no reason to wait
  for Oct 30 to run this one; it can run as soon as it has sign-off, independent of the rest of this
  runbook.
- **Duration-estimated backfill (rides) also has no strict ordering dependency on Oct 30, but is
  naturally idempotent either way.** The booking importer already stamps the marker correctly on
  every row it writes from 2026-08-19 onward, Oct 30's final import included — so by the time Oct 30
  happens, only the original ~186 (2026-07-29) rows will still be missing the marker, and the
  backfill's own plan step only ever targets rows genuinely missing it. Recommendation: it is safe,
  and probably preferable, to run this **before** Oct 30 (closes the gap for the existing 186 rows
  sooner, and gives one clean dry-run/apply cycle to validate against, rather than mixing it with the
  larger Oct 30 cutover event). Running it again **after** Oct 30 as a final safety-net sweep is also
  safe and cheap — the plan step will simply report "0 rows to stamp, all already marked" if nothing
  new needs it, since every Oct 30 row will already carry the marker from the importer-side fix.
- **Neither script needs to run against "the Oct 30 import" specifically** — both scripts scan by
  `legacy_import_metadata->>source`, not by import batch/date, so they naturally pick up every
  matching row regardless of which import wrote it, and skip (idempotently) anything already
  handled.
- **The GST backfill is out of scope for this runbook's sequencing recommendation** — it has no
  commit path yet, so there is nothing to sequence. When one is added, whoever adds it should read
  this runbook and the concurrent-writer change-log entry before proposing a rollout order relative
  to the duration-estimated backfill.

This is a recommendation, not a decision — confirm timing with the product owner before scheduling
either run.

## Pre-flight checklist (before ever passing `--apply`)

1. **Confirm the target environment explicitly.** Both scripts read `SUPABASE_URL` /
   `SUPABASE_SERVICE_ROLE_KEY` from the environment — verify which project those point at
   (`echo $SUPABASE_URL` or check `backend/.env`) before running anything. Never assume; a
   service-role key against the wrong project is a silent wrong-database write.
2. **Run without `--apply` first, always**, and read the printed report in full:
   - SIN/DOB: `python backend/scripts/backfill_legacy_driver_sin_dob.py --banks-csv <path> --drivers-csv <path>`
   - Duration-estimated: `python backend/scripts/backfill_legacy_ride_duration_estimated.py`
3. **Record the dry-run counts before applying** — rows planned, rows skipped (already on file /
   already marked), and any errors or warnings. Compare against what's expected (e.g. "~186 legacy
   rides, some subset missing the marker" for the duration-estimated backfill; a similarly-sized
   population for SIN/DOB, joined against `banks.csv`). A count wildly different from expectation
   (much larger, much smaller, or a spike in errors/warnings) is a stop-and-investigate signal, not
   something to `--apply` through.
4. **If time has passed since the last dry-run**, re-run the dry run immediately before `--apply` —
   more legacy rows may have been imported in the interim (e.g. via Oct 30), and a stale report from
   days or weeks earlier should not be trusted blindly.
5. **Never run both this session's rides-side backfill and a future GST-backfill `--apply` at the
   same time** (see "concurrent-writer risk" above) — run one to completion and verify its report
   before starting the other.
6. **Rollback path, confirmed before running:**
   - SIN/DOB: every updated driver's `id` is printed at apply time. Reverting means, per printed id,
     nulling `sin` / `sin_last4` / `sin_collected_at` / `date_of_birth` and removing the
     `legacy_mongo_banks_sin_dob_import` key from `legacy_import_metadata` — no cascading state
     (no payout, no Stripe call) is triggered either way.
   - Duration-estimated: every updated ride's `id` is printed at apply time. Reverting means, per
     printed id, removing exactly the `duration_estimated` and `legacy_duration_estimated_backfill`
     keys from `legacy_import_metadata` (leaving every other key, including any `old_payout_gst_amount`
     a GST backfill may separately have added, untouched) — `duration_minutes` is never written by
     this script, so there is nothing to revert there either.
   - Neither script's rollback requires a migration or a second deploy — both are plain, targeted
     JSONB-key removals against the printed id list.
7. **Both scripts are safe to re-run after a partial failure** — re-running only ever touches rows
   still missing their respective marker/field, by construction of the plan step.

## Sign-off

**Flipping `--apply` on either script requires explicit product-owner sign-off before it happens.**
This runbook documents the safe procedure; it is not that sign-off, and this session does not
provide it. Whoever eventually applies either script should:

1. Confirm the target environment and expected row counts (pre-flight checklist above) with a fresh
   dry run.
2. Get explicit go-ahead from the product owner for that specific run (environment, expected row
   count, timing relative to Oct 30) — referencing this runbook and the two change-log entries above.
3. Only then pass `--apply`.

No `--apply` run has happened yet for either script, against any environment, as of this document.
