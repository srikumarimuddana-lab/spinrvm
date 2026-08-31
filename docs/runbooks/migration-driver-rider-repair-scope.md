# Driver/rider population-count validation + the driver-repair pass

Owner follow-up (2026-08-31) to two things left open after the Migration
Data Quality Scan / Bulk Operations reorg landed: "validate the 910
driver / 1,938 rider counts" and "build the repair pass that re-checks
unmatched driver/rider phones against current data." This doc covers both:
an admin-portal walkthrough for the first (no raw Mongo export files were
supplied to this session, so a direct file diff wasn't possible), and the
actual, honestly-scoped repair pass built for the second — **driver-side
only**, with the reasoning for why rider-side isn't buildable yet.

## Part 1 — validating the driver/rider population counts

The 910 driver / 1,938 rider figures are live `SELECT count(*)` numbers
against `drivers`/`users` filtered to the legacy-import markers — not a
static report. Nothing in Supabase disputes them; the Migration Checklist
panel re-derives the equivalent per-tool counts every page load
(`GET /api/admin/migration-status`). Two ways to confirm them, in order of
how much they actually prove:

### A. Re-run the importer's own dry-run against the original CSVs (strongest check)

This is the closest thing to a "diff against the Mongo files" this session
can do without the raw export attached to the conversation:

1. **Drivers**: `/dashboard/drivers/import` (Bulk Driver Import, Saskatoon
   CSV) and `/dashboard/drivers/legacy-import` (Legacy Driver Import, Mongo
   `drivers.csv`) both have a Preview/dry-run step *before* any write. Upload
   the same source CSV again and read the preview counts: "N rows read, M
   already imported (matched by `old_driver_id` — skipped, not
   re-created), K new." `M` should equal the count of driver rows this tool
   already created; `K` should be 0 if the CSV hasn't changed since the last
   real import. A non-zero `K` on a re-upload of a file you believe was
   already fully imported is the actual signal to chase, not the top-line
   910 figure itself.
2. **Riders**: same pattern on `/dashboard/bulk-operations`'s Bulk Rider
   Import section — re-upload `customers.csv`, read the preview's
   already-matched vs. new breakdown.

This works because every importer's dry-run is read-only and idempotent by
design (see each tool's own docstring) — re-running it costs nothing and
never double-imports.

### B. Read the Migration Checklist panel counts directly (fastest check)

`/dashboard/bulk-operations` → Migration Checklist panel, steps 1–3, shows
the same counts this doc opened with, computed fresh on every page load
from `migration_status_service.py`. If a number here looks wrong, the
service functions computing it (`_tool_1_bulk_driver_import`,
`_tool_2_legacy_driver_import`, `_tool_3_bulk_rider_import`) are a few lines
each and read straight off `legacy_import_metadata` — worth eyeballing
directly against a manual `SELECT count(*)` in the Supabase SQL editor if a
discrepancy shows up.

### C. Direct file diff (only possible with the raw export)

If the raw Mongo export (`drivers.csv`, `customers.csv`, or the underlying
`.bson`/`.json` dump) is attached to a future session, a real diff is
possible: count distinct `_id`s in the source file, compare against
`drivers`/`users` rows carrying that file's import-source marker. This
wasn't done here because no export file was supplied to this conversation —
option A above is the closest available substitute and doesn't require one.

## Part 2 — the driver-repair pass

**What it does.** New Step 18 tool
(`backend/services/migration_driver_repair_service.py`,
`/dashboard/bulk-operations` → Final review phase → "Driver-repair pass").
For every completed ride still missing a driver
(`migration_data_quality_service`'s `missing_driver` finding) that carries
a recorded `legacy_import_metadata.old_driver_id`, it re-checks the
**current** `drivers` table — not the original CSV — for a driver row now
linked to that same old id, via either linkage shape (a driver created
directly by an importer, or one enriched via `mongo_driver_history`). A
driver added in a later import batch than the ride itself is exactly the
case this recovers: the ride had nothing to match at its own import time.

On commit it does three things together, not just one, because setting
`driver_id` alone would leave two other tables silently stale:

1. Sets `rides.driver_id`.
2. Reconstructs the ride's Period 2/3 `driver_insurance_periods` rows
   (regulatory, 7-year audit retention) — a `missing_driver` ride never got
   these at import time, since the original `_plan_insurance_periods` call
   requires a driver to be present.
3. Writes one offsetting `payouts` row per driver, sized to exactly cancel
   the newly-linked ride's `driver_earnings` — otherwise that driver's live
   `payable_balance` (which sums completed-ride earnings minus payouts)
   would silently increase by real dollars for a trip already settled in
   the old app.

An old driver id claimed by more than one current driver is never guessed
at — excluded as `ambiguous_old_driver_id_skipped`, reported separately, not
linked to either candidate.

**Same posture as Legacy Wallet Import / Pre-Launch Data Flagging**:
super_admin only, Preview→Apply, type-to-confirm gate on commit (this
mutates a real field plus writes to two other tables, unlike the
metadata-only Data Quality Scan).

## Why there is no rider-side repair pass

This was the literal ask ("driver/rider phones") and it does not have an
honest full answer yet. A ride's `legacy_import_metadata` carries
`old_driver_id` (see `booking_import_service.py`), so a driver-side re-match
against current data is possible. There is **no equivalent for riders**:

- No `users` row anywhere in Supabase stores an old-system customer id.
  `rider_import_service.py`'s CSV import never captured one — confirmed by
  querying every distinct top-level key under `users.legacy_import_metadata`
  in production: only `mongo_driver_history` and `rider_csv_import` exist,
  neither of which is a customer-id linkage.
- Migration 328's `legacy_id_crosswalk` table was built specifically to
  solve this — map old-system rider/driver ids to Spinr UUIDs — but it is
  still empty (0 rows), never backfilled (`ACTION_ITEMS.md` A34).

Neither "phone" works as the repair key on either side, contrary to how the
request was phrased: a ride never stores the source booking's raw phone
number, only the old-system id. Phone matching only happens once, at
original import time, before that id linkage is written.

**What would unblock a rider-side repair pass**, in order of effort:

1. Backfill `legacy_id_crosswalk` from the original `customers.csv` (the
   file already has old customer id → nothing yet, since the crosswalk table
   was never populated) — smallest lift if that CSV is still available.
2. Re-supply the raw `customers.csv` export to a session with write access,
   and extend `rider_import_service.py`'s import path to also stamp
   `old_customer_id` onto `users.legacy_import_metadata` the same way
   `booking_import_service.py` already does for `old_driver_id` on
   `drivers` — this closes the gap for *future* imports but still needs (1)
   for rows already imported before this change ships.

Neither is in scope here — this doc exists so the gap is visible and
actionable next time either input becomes available, not re-discovered.
