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

**Actual execution record:** this batch was committed and backfilled for real on 2026-08-29 —
see §8 for what actually happened, including two bugs found and fixed live, a rollback, and the
final real counts (they differ from the estimates in §0/§0a/§2/§4 below, which are left as
originally written for historical context — do not treat them as the final numbers).

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
   - **Already imported** — expect roughly **186**, not the 224 this section previously said.
     Corrected 2026-08-27 after a live `rides` query (`legacy_import_metadata ? 'old_booking_id'`,
     read-only via the Supabase MCP connector against production) returned 186, all `completed`,
     zero `cancelled`/`failed` — matching the figure independently used throughout
     `ACTION_ITEMS.md` since the 2026-08-16 GST backfill, the 2026-08-18 insurance-period
     reconstruction (CR #4081), and the 2026-08-20 verification pass, not the single 224 figure
     recovered from one 2026-07-29 `audit_logs` row. That 224 audit-log figure is real (it's the
     row the import tool itself wrote at commit time) but is not reconciled against the 186 seen
     everywhere else — not root-caused here; flagging so a dry-run reporting ~186 already-imported
     isn't mistaken for 38 rides having gone missing.
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

## 8. Actual execution record — 2026-08-29

This section documents what actually happened when this batch was run for real, including two
bugs hit and fixed live in production and a partial rollback. Sections 1-7 above are left as
originally written (pre-run estimates and the CLI-script-based plan) for historical context —
where they disagree with this section, this section is the ground truth.

### 8.1 Commit — scope mismatch and a 502

The dry-run/commit was run via the admin dashboard's Legacy Booking Import tool (§2-§3), by then
carrying a new **"Include cancelled/failed bookings"** checkbox (defaulting to checked — the
§0a-described scope). Two issues surfaced during this specific run:

1. **502 on first commit attempt.** Root cause: `commit_plan()` batched all planned rows
   (completed + cancelled/failed) into single `.insert()` calls; PostgREST derives one column
   list per call from the union of keys across every row in that call, so any row missing a key
   present on another row in the same batch got that column written as `NULL` — not that row's
   own schema default. Fixed by splitting inserts into homogeneous-by-status batches (see
   `docs/change-log/2026-08-29-legacy-booking-import-cancelled-failed-not-null-fix-and-scope-toggle.md`).
   Re-run after the fix succeeded.
2. **Scope mismatch.** The instruction for this run was "completed rides only" (confirmed no
   separate legacy-app terminology — "completed" is the literal status string used throughout).
   The commit was clicked with the new scope checkbox left at its default (checked), so the
   commit went through with **980 total rows**: 55 completed + 918 cancelled/failed + 7 zero-fare
   `completed` anomalies (legacy bookings recorded as completed with $0 fare — kept as
   `completed` since that's their real legacy status, not reclassified). This was only caught
   *after* commit, when the total didn't match the "completed only" instruction — flagged as a
   process gap: the pre-commit screen should have been checked against the stated intent before
   clicking Commit, not just for internal math consistency.

### 8.2 Rollback — 918 rows deleted, 7 kept

Per explicit instruction ("Roll back the 918, keep the 7 insurance-period rows"), the 918
plain-cancelled rows were deleted directly via SQL (`repositories`/admin-dashboard has no
one-click rollback for this, per §7) — they had zero associated `driver_insurance_periods` rows,
so deletion was safe and clean. The 7 zero-fare-`completed` rows were **kept**, along with their
`driver_insurance_periods` audit rows, which are append-only/immutable per migration 64's
trigger and can never be deleted regardless of the ride record's own fate — this drove the
decision to keep those 7 ride rows rather than delete-and-orphan their insurance-period rows.

**Net result of this run: 62 new rows** (55 regular completed + 7 zero-fare completed), not the
~19 estimated in §0. Combined with the pre-existing legacy-imported rides already in production
(186 from a 2026-07-29 batch + 28 from an earlier import predating the
`legacy_import_metadata->>'imported_at'` field), **production now holds 276 total
legacy-imported rides** (259 `completed` + 17 `cancelled` — the 17 pre-date this run and were not
touched by it).

### 8.3 Route/snapshot backfill — admin-dashboard tools, not the CLI scripts in §4

§4's CLI scripts (`backfill_imported_ride_routes.py` / `backfill_imported_ride_snapshots.py`)
were **not used** for this run — this session's standing constraint rules out shell access +
`SUPABASE_SERVICE_ROLE_KEY` for a live production write. Instead:

- **Snapshots**: the admin dashboard's existing "Regenerate Snapshots" tool (Bulk
  Operations) was used, but its `admin_regenerate_imported_snapshots()` route processed rides
  sequentially and stalled in production at 50/62 rides with no error surfaced to the operator
  (same production-stall bug class found 3 times earlier in this migration's CSV importers).
  Fixed with `asyncio.Semaphore`-bounded concurrency — see
  `docs/change-log/2026-08-29-regenerate-imported-snapshots-sequential-stall.md`. After the fix
  deployed, a re-run succeeded (`200 succeeded` — the tool's own per-run cap; a second click is
  not needed since it only re-processes rides missing a snapshot by default).
- **Routes**: no admin-dashboard tool existed for `planned_route_polyline` at all — a new one was
  built (`POST /api/admin/rides/regenerate-imported-routes`, "Route Backfill" section on Bulk
  Operations), reusing the existing `utils/route_distance.py::compute_route()` (OSRM-first,
  Google Directions fallback — the same function live ride booking uses), built with bounded
  concurrency from the start. See
  `docs/change-log/2026-08-29-imported-ride-route-backfill.md`. Ran clean: `62 succeeded`.

### 8.4 Final verification (direct SQL against production)

```
-- All legacy-imported rides have a snapshot
select count(*) as total, count(*) filter (where route_snapshot_url is not null) as has_snapshot
from rides where legacy_import_metadata is not null;
--> total=276, has_snapshot=276

-- The 62 new rows from this run all have a real route + distance
select count(*) as total_new_batch,
       count(*) filter (where planned_route_polyline is not null
                         and jsonb_array_length(planned_route_polyline::jsonb) > 1) as has_real_route,
       count(*) filter (where distance_km is not null and distance_km > 0) as has_distance
from rides where legacy_import_metadata->>'imported_at' = '2026-08-29T19:44:37.785728+00:00';
--> total_new_batch=62, has_real_route=62, has_distance=62 (14-300 points per route, sane km values)
```

This batch is complete: 62 net-new rows committed (scope-corrected to completed-only intent via
rollback), all 276 legacy-imported rides in production have a map snapshot, and all 62 new rows
have a real road-following route and computed distance.
