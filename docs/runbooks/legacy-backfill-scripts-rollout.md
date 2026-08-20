# Legacy Backfill Scripts — Rollout Runbook

**Status:** written 2026-08-20, updated same day to add a third capability, then updated again same
day to record the product owner's rollout decision (see "Decision recorded" below). All three
covered here have been built, tested against a local fake Supabase client (and, for the booking
import, verified against the real cached `bookings.csv` export's actual row counts), and reviewed —
but **none has ever been run with `--apply`/`commit` against any environment** (mocked, staging, or
production) **by any Claude Code session**. This document describes the safe procedure for whoever
runs them. **It is not, itself, the sign-off to run any of them** — the sign-off now exists (see
"Decision recorded" immediately below), but the actual `--apply`/commit execution is the product
owner's own action, still outstanding as of this update.

## Decision recorded (2026-08-20)

Put to the product owner directly via `AskUserQuestion` (not inferred or assumed):

1. **Timing for the two CLI backfill scripts (SIN/DOB, duration-estimated): run now, or wait for
   the Oct 30 final cutover?** → **Run now.**
2. **Which `bookings.csv` vintage should the cancelled/failed booking import run against?** →
   **Run now, against the existing 2026-07-26-vintage export** — not wait for a fresh Oct 30 pull.
   Accepted trade-off (see "The three capabilities" §3 and "Recommended run order / sequencing"
   below, both unchanged by this decision): anything cancelled/failed between 2026-07-26 and Oct 30
   will need a second, harmless, idempotent commit pass against the later export.
3. **Execution: this session has no live Supabase credentials and cannot run any `--apply`/commit
   itself. How should the runs actually happen?** → **The product owner will run them directly**,
   using this runbook (pre-flight checklist, dry-run-first, rollback path per capability) — not
   delegated to a Claude Code session.

**What this decision does *not* cover:** the underlying legal-sufficiency judgment on old-app consent
(business/counsel decision) remains open and unrelated to this rollout decision. The 7-anomalous-row
disposition, open at the time this section was first written, was resolved the same day — see "The
three capabilities" §3 above; it does not need a separate decision before running this runbook's
capabilities.

**As of this update, no `--apply`/commit run has actually happened yet** — this section records the
go-ahead and the execution plan, not a completed run. The pre-flight checklist, rollback paths, and
sign-off steps below remain exactly as written and still apply to every run.

Related reading:
- `docs/change-log/2026-08-19-legacy-duration-estimated-backfill.md` — build history for the
  duration-estimated backfill, including the concurrent-writer risk this runbook accounts for.
- `docs/change-log/2026-08-19-legacy-backfill-concurrent-writer-fix.md` — the code-level fix for
  that risk.
- `docs/change-log/2026-08-20-legacy-cancelled-failed-booking-import.md` — build history for the
  cancelled/failed booking import capability (§3 below).
- `docs/runbooks/legacy-migration-playbook.md` — the broader Oct 30 final-cutover playbook (not
  modified by this document; read it for the full migration timeline these three capabilities sit
  inside).

## The three capabilities

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

### 3. Cancelled/failed legacy booking import (`booking_import_service.py` + existing admin `/bookings/import/*` endpoints)

Not a standalone CLI script — this reuses the same admin-triggered, four-CSV upload flow
(`bookings`/`customers`/`drivers`/`driver-earnings`) already in production for completed-booking
import, super-admin-only, at `POST /api/admin/bookings/import/validate` and `.../commit`. As of
2026-08-20 the same `bookings.csv` upload also imports `cancelled`/`failed` legacy bookings as
`rides.status='cancelled'` rows — GPS, timestamps, and cancellation attribution only, explicitly no
fare/earnings/payout write. Of the real export's 1,210 rows: 712 `cancelled` + 225 `failed` are
targeted (minus phone-match/test-account skips, which can only be known against a live `users`/
`drivers` table); 2 blank-status rows are excluded (genuinely unknown status, unsafe to guess).

**7 anomalous "failed-but-actually-completed" rows — disposition decided 2026-08-20, not excluded
any more.** These 7 (real `driver_id`/`start_ride_at`/`complete_delivery_at`, structurally a
completed trip) were found permanently excluded as of the first cut of this capability. A same-day
follow-up (`docs/change-log/2026-08-20-anomalous-legacy-rows-payment-verification.md`) cross-checked
the old app's own `payments.csv` export and found 0 of the 7 (0/225 of the whole `failed` bucket) has
any payment record — the trip happened but was never paid for. Product owner decided (via
`AskUserQuestion`) to import them as `rides.status='completed'` with real GPS/distance/duration but
**$0 fare, $0 driver earnings, no payout** — see
`docs/change-log/2026-08-20-anomalous-rows-zero-fare-completed-import.md` for the implementation.
These 7 now commit alongside the other 937 cancelled/failed rows in the same `commit` call — no
separate action needed to include them. **Status: built, unit-tested against 76 tests (across both
booking-import test files) plus the real cached CSV's row counts for the base cancelled/failed path,
reviewed by `spinr-migration-reviewer` and `spinr-money-auditor` (no blockers), never run against a
live environment.**

This capability is **additive to the same CSVs already used for the completed-booking import** — it
does not require a separate upload or a separate decision about *which* CSV to use, only whether/when
to actually commit against real data. If the completed-booking import has already been run against a
given CSV batch, re-running `commit` against the *same* CSVs now will import the newly-supported
cancelled/failed rows from that same file (the already-imported completed rows are skipped
idempotently, matched on `old_booking_id`) without re-touching anything already committed.

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

**The cancelled/failed booking import does not share this risk.** It `INSERT`s new `rides` rows
(each keyed on a fresh, never-before-used `id`); it never reads-merges-writes an existing row's
`legacy_import_metadata`, so it cannot race the duration-estimated backfill or a future GST backfill
the way those two can race each other. It can run before, after, or concurrently with either backfill
with no special sequencing needed on that front.

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
- **The cancelled/failed booking import has a real timing question the other two don't: which CSV
  vintage to import against.** The only `bookings.csv` available today is the 2026-07-26-vintage
  export (the same one that fed the 2026-07-29 production cutover) — it does not reflect any booking
  activity since then, and it is *not* a fresh pull (confirmed in the original collection-inventory
  audit). Two real options, not a recommendation either way — this is the product owner's call:
  1. **Run now, against the existing CSV.** Gets 712+225 cancelled/failed rows of rider/driver trip
     history and GPS-retention coverage into Spinr sooner. Trade-off: anything cancelled/failed
     between 2026-07-26 and whenever Oct 30's fresh export lands will need a *second* commit pass
     against that later CSV anyway (harmless — idempotent, matched on `old_booking_id`, no duplicate
     risk — just two operator actions instead of one).
  2. **Wait for the Oct 30 final export, run once.** One pass, current data, no need to think about
     which of two CSVs is "the" source of truth for cancelled/failed rows. Trade-off: the regulatory
     GPS-retention benefit this capability exists for (see the change log) is deferred by however long
     the wait to Oct 30 is.
  Either way, the completed-booking import's own established four-CSV upload flow is unchanged —
  this is purely a "when to click commit" question, not a new operational procedure to design.

This is a recommendation, not a decision — confirm timing with the product owner before scheduling
any of the three.

## Pre-flight checklist (before ever passing `--apply`/`commit`)

1. **Confirm the target environment explicitly.** The two CLI scripts read `SUPABASE_URL` /
   `SUPABASE_SERVICE_ROLE_KEY` from the environment; the booking import runs as an authenticated
   admin-dashboard action against whichever backend the admin's session is pointed at — verify which
   project/environment either way (`echo $SUPABASE_URL`, check `backend/.env`, or confirm the admin
   dashboard's own environment banner) before running anything. Never assume; a service-role key or
   admin session against the wrong project is a silent wrong-database write.
2. **Run without `--apply`/commit first, always**, and read the printed/returned report in full:
   - SIN/DOB: `python backend/scripts/backfill_legacy_driver_sin_dob.py --banks-csv <path> --drivers-csv <path>`
   - Duration-estimated: `python backend/scripts/backfill_legacy_ride_duration_estimated.py`
   - Cancelled/failed booking import: `POST /api/admin/bookings/import/validate` with the same four
     CSVs (the admin dashboard's existing Legacy Booking Import tool already calls this first, before
     enabling its own "commit" action).
3. **Record the dry-run counts before applying** — rows planned, rows skipped (already on file /
   already marked / test-account for the booking import), the new `cancelled_failed_zero_fare_completed`
   count (should be up to 7, matching the anomalous rows — see §3 above), and any errors or warnings.
   Compare against what's expected (~186 legacy rides for the duration-estimated backfill; a
   similarly-sized population for SIN/DOB joined against `banks.csv`; up to 712 cancelled + 225 failed
   minus phone-match/test-account skips and minus 2 blank-status for the booking import, of which up
   to 7 of the "failed" ones land in the zero-fare-completed bucket instead of the normal
   cancelled/failed one). A count wildly different from expectation (much larger, much smaller, or a
   spike in errors/warnings) is a stop-and-investigate signal, not something to apply through.
4. **If time has passed since the last dry-run**, re-run the dry run immediately before applying —
   more legacy rows may have been imported in the interim (e.g. via Oct 30), and a stale report from
   days or weeks earlier should not be trusted blindly.
5. **Never run both the duration-estimated backfill and a future GST-backfill `--apply` at the same
   time** (see "concurrent-writer risk" above) — run one to completion and verify its report before
   starting the other. The booking import doesn't share this constraint (it `INSERT`s, doesn't
   read-merge-write) and can run alongside either.
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
   - Cancelled/failed booking import: the commit response returns every inserted ride's `id` (same
     shape as the existing completed-booking import's response). Reverting means deleting exactly
     those `rides` rows by id — this path never writes a `payouts` row (unlike the completed-booking
     import, which does have an offsetting-payout row to consider on rollback). The normal
     cancelled/failed rows also never touch `drivers.total_rides`; the up-to-7 zero-fare-completed rows
     (§3 above) do get counted into it via the same recount used by the regular completed path, so
     reverting those specifically also needs a re-run of `recount_driver_total_rides` (or the next
     scheduled import) for any driver among the deleted ids to bring the count back down.
   - None of the three requires a migration or a second deploy to roll back — all are plain, targeted
     writes against a printed/returned id list.
7. **All three are safe to re-run after a partial failure** — re-running only ever touches rows
   still missing their respective marker/field/row, by construction of each's plan/validate step.

## Sign-off

**Flipping `--apply`/commit on any of the three requires explicit product-owner sign-off before it
happens.** That sign-off now exists — see "Decision recorded" at the top: run all three now, the
booking import against the existing 2026-07-26-vintage CSV, executed by the product owner directly
(no live Supabase credentials are available to any Claude Code session). This runbook is the
documented safe procedure for that execution, not a substitute for it. Before each actual run:

1. Confirm the target environment and expected row counts (pre-flight checklist above) with a fresh
   dry run/validate — the "Decision recorded" go-ahead is not a substitute for this per-run check.
2. Re-confirm nothing has materially changed since 2026-08-20 (e.g. a fresher CSV export becoming
   available, in which case revisit whether the "run against the existing export" choice above still
   holds) before passing `--apply`/click commit.
3. Only then pass `--apply`/click commit.

No `--apply`/commit run has happened yet for any of the three, against any environment, as of this
document (last updated 2026-08-20, decision recorded).
