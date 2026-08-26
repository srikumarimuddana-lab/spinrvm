# Legacy Ride History Presentation — Implementation Plan

**Status:** planning only — no code changed yet. This plan covers items #1–#3 from the
2026-08-25 migration-batch-readiness session: committing the net-new legacy rides, and
surfacing "imported ride" history honestly to admins (already done), riders, and drivers
(not yet done). Item #4 (`payout_gst_amount` presentation, reopening the reviews/chats
exclude decisions) is a separate business/legal call, deliberately **not** in this plan.

## 1. Goal

For every legacy-imported ride (`rides.legacy_import_metadata != '{}'`), a rider or driver
looking at that ride's detail screen should see the same honest picture admin already
sees: an "Imported" indicator, pickup/dropoff pins, and a clear disclaimer that no live GPS
trail exists for the trip — never a route line that looks like it was actually tracked.

## 2. What already exists (verified in this session, not assumed)

This is smaller than it first looked, because most of the pipeline is already built:

- **`booking_import_service.py`** already writes `pickup_lat`/`pickup_lng`/`dropoff_lat`/
  `dropoff_lng`/`pickup_address`/`dropoff_address` onto every imported `rides` row, straight
  from `bookings.csv`'s own `pickup_lat`/`pickup_long`/`drop_lat`/`drop_long`/`pickup_address`/
  `drop_address` columns — **not** derived from `driverlocationlogs` at all.
- **`scripts/backfill_imported_ride_routes.py`** already turns those two points into a real
  road-following route via OSRM and stores it in `rides.planned_route_polyline` — this is
  the established "pickup/dropoff only" pattern already in production for the 224 rides
  imported so far. It does not touch `driverlocationlogs`.
- **`scripts/backfill_imported_ride_snapshots.py`** already renders a static-map PNG of that
  planned route into `rides.route_snapshot_url`.
- **Admin-dashboard already presents this correctly**: `ride-detail-modal.tsx` computes
  `isImported = !!ride?.legacy_import_metadata && Object.keys(...).length > 0`, shows an
  "Imported" badge, and passes `suppressStraightFallback={importedNoGps}` into
  `ride-route-map.tsx` so the map draws **only** the pickup/dropoff pins plus the planned
  OSRM route (dashed, clearly labeled), never a fabricated straight-line "as the crow flies"
  connector, with the caption *"Imported from the previous app — no GPS was recorded for
  this ride."*
- **Rider-app (`ride-details.tsx`) and driver-app (`ride-detail.tsx`) already read
  `ride.planned_route_polyline`** and render it on the map (lines 242–246 and 117–123
  respectively) — they are not missing the data pipeline. What they're missing is the
  *honesty layer*: neither reads `legacy_import_metadata`, neither shows an imported badge,
  and neither has the "no GPS was recorded" disclaimer, so a legacy ride's planned route can
  currently render indistinguishably from a real captured GPS trail.

**Net effect:** this plan is a presentation-layer fix on top of an existing, working data
pipeline — not a new import or a new route-generation system.

## 3. Scope (per this session's decisions)

- **GPS detail level: pickup/dropoff + planned road route only** (the existing OSRM-backfill
  pattern) — no raw `way_points` GPS trail, matching CLAUDE.md's 3-year, pickup/dropoff-only
  retention rule. `driverlocationlogs` is not a dependency for this plan.
- **Surfaces: admin (done) + rider-app + driver-app**, all at once, per the earlier decision.
- **Population: the 224 already-migrated rides (already covered) + the 19 net-new completed,
  Canada-matched rides in the 2026-08-22 export**, once committed.

## 4. Work items

### Item 1 — Commit the net-new batch (operational, not code)

`routes/admin/booking_import.py` + the admin-dashboard's existing "Legacy Booking Import"
tool (`bulk-operations/_components/LegacyBookingImport.tsx`) already do exactly this job:
validate → dry-run report → commit, idempotent on `rides.legacy_import_metadata->>'old_booking_id'`
(a re-sent commit converges, it doesn't duplicate), writes offsetting `payouts` rows so the
net payable delta is $0, super-admin gated, audit-logged.

Steps (no new code):
1. Upload the 08-22 export's `bookings.csv`, `customers.csv`, `drivers.csv`, and
   `driverearnings.csv` through the existing tool's **validate** step — review the dry-run
   report (expect ≈19 new completed Canada rows to actually commit; the tool itself decides
   what's new via the `old_booking_id` check, not a pre-filtered file).
2. Commit.
3. Re-run `scripts/backfill_imported_ride_routes.py` and
   `scripts/backfill_imported_ride_snapshots.py` (already idempotent/`--force`-gated) so the
   new rides get the same `planned_route_polyline`/`route_snapshot_url` treatment as the
   original 224.

This is a live-data-writing action against production Supabase — I don't have that access in
this session, and per CLAUDE.md's caution-over-speed default for anything touching rides,
this should be run by whoever holds super-admin access, not scripted around. Flagging it here
so it's tracked as a real to-do, not silently skipped.

### Item 2 — Backend: dark-ship flag for the badge

`legacy_import_metadata` is already returned on every ride response today — unflagged, since
it's just an existing column. This item doesn't gate *new data exposure*; it gates the
*client-visible badge/disclaimer UX*, following the exact precedent already in this repo:
`routes/legacy_consent.py`, dark-shipped on `app_settings.legacy_consent_notice_enabled`
(default `False`).

Proposed: add `app_settings.legacy_ride_badge_enabled` (default `False`), and compute a
`show_legacy_badge: bool` field server-side wherever a rider/driver ride-detail response is
serialized — `True` only when the flag is on **and** `legacy_import_metadata` is non-empty.
This means rider-app/driver-app need no new network call and no client-side flag-fetching
logic; they just render conditionally on a field that's `False` until explicitly flipped on.

**Files (≤3):** the ride-detail serialization path in `routes/rides/` (exact call site to be
confirmed at implementation time — `receipts.py`/`payments.py` or wherever the rider/driver
ride-detail GET already reads `legacy_import_metadata`), plus a migration or `app_settings`
seed row for the new key, plus a test.

### Item 3 — Rider-app: imported-ride badge + honest map state

**File:** `rider-app/app/ride-details.tsx` (+ a test file, ≤3 total).

- Read `ride.show_legacy_badge` (from Item 2). When true:
  - Show an "Imported" badge next to the ride status, mirroring admin-dashboard's copy.
  - Suppress whatever "route quality" language currently assumes a captured GPS trail: use
    the existing `plannedSegments`/`isV2Route` branching already in the file (lines 351–414)
    but add the explicit disclaimer text under the map, matching admin-dashboard's
    *"Imported from the previous app — no GPS was recorded for this ride"* wording so the
    two surfaces read consistently.
  - No change to `mapCoordinates`/`RouteLine` rendering logic itself is required — the
    planned route already renders; this item only adds truthful labeling around it.

### Item 4 — Driver-app: same treatment

**File:** `driver-app/app/driver/ride-detail.tsx` (+ a test file, ≤3 total). Same badge +
disclaimer pattern as Item 3, adapted to this file's existing map section (lines 217–270).

## 5. Copy (reuse verbatim from admin-dashboard for consistency)

- Badge label: **"Imported"**
- Disclaimer: **"Imported from the previous app — no GPS was recorded for this ride"**
- (Admin-dashboard's per-phase labels — "Not captured (imported)" — apply only where a
  phase-distance breakdown exists; rider/driver-app's simpler ride-details screens don't
  need to replicate that level of detail unless later requested.)

## 6. Explicitly out of scope here

- `payout_gst_amount` presentation (needs a business/legal decision on the two-GST-component
  question — tracked separately).
- Reopening the `reviews`/`chats`/`complaints` exclude decisions from the 08-19 inventory —
  those stand unless explicitly revisited.
- `banks.csv` — no change to the existing SIN/DOB-only, no-raw-banking minimization decision.
- Full `way_points` route detail — deliberately excluded per the pickup/dropoff-only decision;
  would need its own clean re-extract and its own retention-policy exception if ever revisited.

## 7. Verification plan (per item, before merge)

- **Item 1**: dry-run report reviewed (row counts match the expected ~19, zero unexplained
  errors); after commit, spot-check a handful of new rides in admin-dashboard show correct
  fare/pickup/dropoff before running the route/snapshot backfills.
- **Item 2**: unit test that `show_legacy_badge` is `False` with the flag off regardless of
  `legacy_import_metadata`, and `True` only with both the flag on and metadata present.
- **Items 3–4**: component/screen test asserting the badge and disclaimer render only when
  `show_legacy_badge` is true, and that a non-legacy ride's screen is pixel-for-pixel
  unchanged (no visual regression tooling exists for either app — reasoned about via
  snapshot/screenshot in the PR, not automated, per CLAUDE.md's standing gap note).

## 8. Rollback plan

- Item 1: the import tool is additive-only (new `rides`/`payouts` rows); rollback is deleting
  the specific `old_booking_id`-tagged rows if ever needed — no schema change involved.
- Item 2: flip `app_settings.legacy_ride_badge_enabled` back to `False` — instant, no
  redeploy, matches the existing `legacy_consent` pattern.
- Items 3–4: flag-gated client code; a `git revert` is safe since nothing is written, only
  read and displayed.

## 9. Before this ships

Each item needs its own Change Impact Log entry per `docs/templates/CHANGE_IMPACT_LOG.md`
when actually implemented (this planning doc is not a substitute for it) — Items 3–4 touch
live-tested rider/driver-app screens and need the "User experience effect" field filled in
explicitly even though the change is additive.
