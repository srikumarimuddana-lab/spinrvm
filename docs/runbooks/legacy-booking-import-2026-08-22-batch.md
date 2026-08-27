# Runbook: Import the 2026-08-22 Legacy Booking Batch

**Scope (expanded 2026-08-27 — see §0a):** committing every net-new legacy booking — completed
**and now also cancelled/failed** — from the `Mongo_20260822-DrivelocLess` export into `rides`
(+ offsetting `payouts` for the completed ones only), then backfilling planned routes and map
snapshots for the completed ones.
**Domain:** rides (legacy migration)
**Tools used:** admin-dashboard's existing "Legacy booking import" tool (Bulk Operations page);
`backend/scripts/backfill_imported_ride_routes.py`; `backend/scripts/backfill_imported_ride_snapshots.py`.
**No new code is involved** — everything below uses tools already shipped and tested in
production, including the cancelled/failed branch (already live in
`backend/services/booking_import_service.py`, simply never run against the full booking set
before). This is an operational runbook, not a deploy.
**Requires:** super-admin access to the admin dashboard, and either shell access to run the two
backfill scripts against the backend server, or someone who can trigger them.

---

## 0. Why this exists

The 2026-08-25 migration-batch-readiness session found that this export contains **19 net-new
completed, real-Canada bookings** beyond the 224 rides already in production (243 total match
the same filter the 224-row production batch was originally cut with — see
`docs/audit/2026-08-25-mongodb-08-22-export-drift-batch-readiness.md`). The import tool itself
doesn't need that number pre-computed — it re-derives what's new by checking each booking's
`old_booking_id` against what's already imported — but it's given here so the dry-run report in
step 2 can be sanity-checked against an expected ballpark instead of taken on faith.

## 0a. Scope expanded 2026-08-27 — cancelled/failed bookings now included

Of the export's 1,210 total bookings, only 271 (22%, `completed`) have ever been imported. The
other **941 (78%)** — 712 `cancelled` + 225 `failed` + 2 blank-status — were deliberately
excluded from every prior run of this import. That exclusion has been reversed (see
`docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §2): every cancelled/failed
row still carries real pickup/dropoff GPS and a `created_at` timestamp that PIPEDA/SK
Transportation Act retention rules require Spinr to keep, and the import code to handle them
safely already exists and is already live (writes `status='cancelled'`, no fare, no earnings,
no payout, no driver recount — see the module docstring in `booking_import_service.py` for the
full behavior). **This means a fresh dry-run against this export will now show a much larger
"Rides to import" number than the ~19 quoted in §0** — potentially close to the full 941, minus
whatever was already covered by a prior run (none, to date) plus/minus the usual 08-22-vs-07-26
drift. Do not treat a large number here as a red flag the way §6 originally warned about — with
this scope expansion, a large number is now the *expected* outcome, not a sign something is
wrong. Read the report's breakdown between completed-rides-imported and cancelled/failed-rides-
imported (the tool reports both counts separately) rather than eyeballing one combined total.

## 1. Files needed

Four CSVs from the `Mongo_20260822-DrivelocLess` export (**not** `driverlocationlogs.csv` — not
used by this import):

| File | Purpose |
|---|---|
| `bookings.csv` | One row per legacy booking |
| `customers.csv` | Supplies rider phone numbers for matching to Spinr accounts |
| `drivers.csv` | Supplies driver phone numbers for matching to Spinr accounts |
| `driverearnings.csv` | Actual driver payout per booking |

All four are required — the import tool refuses to build a plan from `bookings.csv` alone.

## 2. Validate (dry run — no writes)

1. Go to **Admin Dashboard → Bulk Operations → Legacy booking import**.
2. Upload all four CSVs above.
3. Leave **Service area** as `Saskatoon` and **Vehicle type** as `Economy` (the tool's own
   defaults) unless told otherwise.
4. Click **Validate (no writes)**.
5. Read the report:
   - **Rides to import** — per §0a, expect this to be much larger than the original ~19 estimate
     now that cancelled/failed bookings are in scope — potentially close to 941 (before
     accounting for the usual 08-22-vs-07-26 drift and unmatched/error exclusions). A number at
     or near **0** is the actual red flag now (suggests the wrong files were uploaded, or the
     tool silently reverted to completed-only) — see §6.
   - **Already imported** — expect roughly **224** (the completed rows already committed; there
     is no prior cancelled/failed run to also show up here yet).
   - Check the report's **completed vs. cancelled/failed sub-counts separately** if the tool
     surfaces them — a plausible split is ~19 net-new completed alongside up to ~941 net-new
     cancelled/failed, not one combined number.
   - **Unmatched riders** / **Unmatched drivers** — nonzero is expected and not blocking; those
     bookings still import (as long as at least one side matches), just without a linked
     account on the unmatched side.
   - **Offset total** must be checked against **Driver earnings** — the tool's own warning
     banner explains why: each matched driver also gets one offsetting payout so imported
     earnings never become withdrawable a second time, and the two totals should reconcile.
   - **Errors** (if any) **block commit** — download them (the report has a CSV export button)
     and read every row's message before proceeding. Warnings do not block.

## 3. Commit

Only after the dry-run report looks right per §2:

1. Type `IMPORT` into the confirmation field (case-insensitive, the tool uppercases it).
2. Click **Commit import**.
3. Confirm the success banner reports **imported_rides ≈ 19** and **offset_payouts** matching
   the dry run's numbers.

This step is idempotent — if you need to re-run it (e.g. after a partial failure), re-upload the
same four files and validate/commit again; already-committed rows are skipped automatically
(matched on `rides.legacy_import_metadata->>'old_booking_id'`), so a re-commit converges rather
than duplicating rows or double-paying drivers.

## 4. Backfill routes and map snapshots for the newly imported rides

The commit in §3 populates pickup/dropoff coordinates and addresses directly from
`bookings.csv`, but **not** the road-following planned route or its map image — those come from
two existing scripts that already ran for the original 224 rides and need to run again to cover
the ~19 new ones:

```bash
cd backend

# 1. OSRM road route for every imported ride that doesn't have one yet
python scripts/backfill_imported_ride_routes.py --dry-run   # review first
python scripts/backfill_imported_ride_routes.py              # then for real

# 2. Static-map PNG snapshot of that route
python scripts/backfill_imported_ride_snapshots.py --dry-run
python scripts/backfill_imported_ride_snapshots.py
```

Both scripts only touch rides with `legacy_import_metadata != '{}'` and skip rides that already
have the field they'd set (`planned_route_polyline` / `route_snapshot_url` respectively) unless
`--force` is passed — so running them again over the full set of 243 (224 old + ~19 new) is safe
and only does real work for the new rows. Requires `OSRM_URL` (or `OSRM_FALLBACK_URL`) reachable
from wherever you run script 1, and `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` plus a Google
Maps API key (in the `app_settings` table) for script 2.

## 5. Verify

- In Admin Dashboard → Rides, filter to recently created rides and spot-check a handful of the
  newly imported ones: fare fields populated, pickup/dropoff addresses present,
  `legacy_import_metadata` non-empty.
- Confirm `planned_route_polyline`/`route_snapshot_url` are populated on a few of the new rides
  after step 4 (previously null right after step 3, since that's what step 4 exists to fill in).
- If the rider-app/driver-app "Imported" badge feature (this session's other work, PRs #4557/
  #4558, both merged but flag-gated off by default) is ever turned on via
  `app_settings.legacy_ride_badge_enabled`, these new rides will pick it up automatically — no
  separate action needed for that.

## 6. If the dry-run numbers look wrong

- **Rides to import ≈ 0, Already imported ≈ 0 too:** likely uploaded the wrong file set (e.g.
  the 07-26-vintage export instead of 08-22) — check the CSVs are actually from
  `Mongo_20260822-DrivelocLess`.
- **Rides to import ≈ 19-ish only (no cancelled/failed counted), Already imported ≈ 224:** this
  is the *old* pre-§0a expected shape — it means the deployed backend doesn't yet have the
  cancelled/failed branch of `build_plan()` live (check the backend's deployed commit against
  `backend/services/booking_import_service.py` on `main`), not that anything about this run is
  wrong. Confirm before assuming the scope expansion actually reached production.
- **Rides to import close to 1,210 (the full booking count), Already imported ≈ 0:** the tool
  isn't recognizing the existing 224 completed rows as already-imported — stop and investigate
  before committing (this would mean a fresh, wrong import against rows that already exist,
  which the tool's own idempotency check should prevent; treat a report like this as a bug, not
  something to push through).
- **Errors on rows that shouldn't have any:** read the specific `field`/`message` — the importer
  hard-requires `pickup_lat`/`pickup_long`/`drop_lat`/`drop_long` and a completion timestamp; a
  handful of legitimate skips for malformed rows is expected and does not block the rest of the
  batch (only rows with errors are excluded from `rides_to_import`, not the whole run).

## 7. Rollback

- **Before commit (§2 only ran):** nothing to roll back — validate never writes.
- **After commit:** the imported rides and their offset payouts are additive rows tagged
  `legacy_import_metadata->>'old_booking_id'`; removing a specific batch means deleting those
  tagged rows and their corresponding `payouts` rows for the same `old_booking_id`s — no schema
  change to revert, no migration involved. There is no one-click rollback in the admin UI for
  this; treat it as a manual, reviewed data cleanup if ever needed, not a routine action.
- **After step 4 (backfill scripts):** `planned_route_polyline`/`route_snapshot_url` can simply
  be cleared (`NULL`) on the affected rides and the scripts re-run later; the underlying ride
  rows are unaffected either way.
