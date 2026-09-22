# 2026-09-22 — Dispatch self-offer exclusion and batch cancellation period close

## Issue and root cause

The live dispatch path could include a driver's account when its `user_id`
matched the ride's `rider_id`. This allowed the rider to receive an offer for
their own ride. The same candidate result feeds primary ranking and claiming;
the vehicle-upgrade cascade is a separate provider query and needed the same
guard. `DispatchService.find_candidate_drivers` is another candidate entry
point and also lacked the exclusion.

Batch dispatch records the driver's Period 2 obligation on each live offer.
When a rider cancelled, the batch-offer cleanup marked pending rows cancelled
but called `set_driver_available` directly, leaving the insurance-period
helper unused and the open Period 2 row unclosed. A read followed by a blanket
release also could have released an offer that changed to accepted in between.

## Fix

- Exclude candidates whose `drivers.user_id` matches `rides.rider_id` after
  each live candidate-provider read, before ranking or claiming, including the
  upgrade cascade. Apply the same guard in `DispatchService`.
- Emit a bounded aggregate rejection log (`reason`, rejected count, remaining
  count) without names, coordinates, or driver identifiers. Existing pre- and
  post-filter pool counts remain available for diagnosing empty matches.
- Replace the batch cancellation read-then-update with a conditional update
  that returns changed rows. Only rows this request changed are sent to the
  transactional release RPC. The RPC checks the cancelled ride and offer,
  current Period 2 ride identity, claim timestamp, other live offers/rides,
  and online state before releasing availability and closing the period
  atomically. It fails closed on uncertainty and invalidates both driver
  cache keys after a successful release.
- Serialize pending-offer inserts and Period 2 transitions against the ride
  state row. A cancelled ride cannot acquire a late pending offer or have a
  delayed Period 2 write reopen coverage after cancellation.
- Log an unexpected period-release exception and still deliver cancellation
  notifications for that offer. The helper itself already handles its normal
  audit-write failures as specified in its contract.

## Alternatives considered

- Filtering only in provider SQL would duplicate policy across the legacy,
  H3/PostGIS, and failover implementations. Filtering at the common
  post-provider boundary avoids provider drift; `DispatchService` gets its own
  equivalent boundary because it is an independent API.
- Releasing availability in Python and closing the period separately would
  permit a concurrent newer claim to be released or misattributed. A
  database transaction holding the driver row lock keeps those checks and
  writes together.
- Releasing every row found by a pre-read would retain the cancellation/accept
  race. The conditional update's returned rows are the ownership token for
  cleanup and notifications.

## Risk, blast radius, and rollback

- **Blast radius:** candidate filtering in the primary live dispatch path,
  vehicle cascade, and standalone `DispatchService`; pending batch-offer
  cancellation, availability, and insurance-period bookkeeping. Migration
  442 adds a ride-state guard, pending-offer trigger, and service-role-only
  release RPC. No historical period rows are changed. No matching radius,
  ranking weights, Redis presence semantics, offer thresholds, or client
  contracts changed.
- The behavioral change affects only rides where rider and driver accounts
  are the same user, plus cancellation of pending batch offers. A self-driver
  exclusion can turn a match into the existing no-driver retry/cascade flow.
- **Rollback:** revert the dispatch and cancellation/runtime commits, then
  drop migration 442's trigger and RPC and restore the transition function
  from migration 421. Rollback does not edit historical period rows.
  Reverting cleanup can leave pending-offer Period 2 rows open; identify and
  review them before any correction to preserve the append-only audit trail.

## Verification

- Added regression coverage showing rider-owned drivers cannot be claimed by
  the primary path or the vehicle cascade, and are removed by
  `DispatchService.find_candidate_drivers`.
- Added cancellation-vs-accept coverage: only rows returned by the atomic
  pending-to-cancelled update are released, so a concurrently accepted offer
  is not released or period-closed.
- Added coverage that a release-helper exception is logged while cancellation
  notifications continue.
- Added PostgREST builder-contract coverage and real-Postgres direct-pool
  cases for cancelled-ride claim rollback and cancelled batch-offer period
  release. Direct-Postgres tests skip when no disposable database is set.
- Updated the Period-2 ride-identity direct-Postgres fixture to seed pending
  offers for its searching rides, matching migration 442's live-obligation
  precondition without weakening the identity assertions.
- Focused tests run: `test_rides_matching_coverage.py` rider-owned primary and
  cascade cases; `services/test_dispatch_service.py` candidate service class;
  `test_ride_cancellation_branches.py` batch cancellation, helper-exception,
  and push-notification cases. All passed.
- Insurance-period and batch-cancellation focused run: 18 passed. Direct
  PostgreSQL tests: 26 skipped because there is no native PostgreSQL DSN in
  this workspace. Migration 442 ran against disposable PGlite PostgreSQL
  18.3 and its ownership/state invariant cases passed; this does not establish
  two-session scheduling under native PostgreSQL.

## Files changed

| File | Change |
|---|---|
| `backend/routes/rides/matching.py` | Provider-agnostic rider-owned exclusion on primary and cascade candidates; aggregate rejection observability |
| `backend/services/dispatch_service.py` | Rider-owned exclusion before presence/ranking filters |
| `backend/routes/rides/cancellation.py` | Conditional pending-offer cancellation, returned-row release through insurance period helper, and release-error logging |
| `backend/utils/insurance_periods.py` | Fail-closed transactional release helper, stale-ride logging, and cache invalidation |
| `backend/migrations/442_release_cancelled_batch_offer.sql` | Ride-state guard, pending-offer serialization trigger, and ownership-checked atomic release RPC |
| `backend/tests/test_rides_matching_coverage.py` | Primary and cascade no-self-offer regressions |
| `backend/tests/services/test_dispatch_service.py` | Candidate service exclusion regression |
| `backend/tests/test_ride_cancellation_branches.py` | Conditional release race, notification resilience, and PostgREST builder API regression |
| `backend/tests/test_insurance_periods.py` | Ownership RPC, fail-closed, and cache invalidation regressions |
| `backend/tests/direct_pool/` | Native-PostgreSQL claim rollback/release cases and migration fixture update |
