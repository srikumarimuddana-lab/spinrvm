# Change Impact & Risk Log — Cross-service-area ride guard

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code (agent), on report from live app testing |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | Follow-up to `8a95c81` (PR #3746, already merged) — branch `claude/cross-service-area-ride-guard-zx1zyp` |
| Related issue or gap ID | Live-testing report: Saskatoon-approved driver received a Regina ride offer |

> **Read this first.** PR #3746 merged the *first* version of this guard as
> `8a95c81`. That version used a bare `service_area_id IN (...)` predicate, which
> excludes every driver whose area is NULL (SQL `IN` never matches NULL) and has
> no kill switch. **That code is on `main` now.** This entry covers the follow-up
> that makes the predicate NULL-safe, adds the flags, fixes the sibling-area case,
> adds the accept-time gate, and replaces a test that passed with the guard
> deleted. Production data (§9) shows the merged version is not currently causing
> an outage — 0 of 153 active drivers have a NULL area — but that is luck, not
> design: the signup path still never sets the column.

## 1. Issue / gap identified

A driver approved for Saskatoon, physically in Regina, received a ride offer when a Regina
rider booked. Driver approval is per service area (municipal licensing, SGI ride-share
endorsement, background check filed with the right regulator), so that driver is not
authorised to carry that trip. Reported from live app testing.

## 2. Root cause

Dispatch never checked the driver's approved service area. The candidate query in
`routes/rides/matching.py` filtered on `is_online`, `is_available`, `is_verified`,
`status='active'`, `vehicle_type_id`, and a lat/lng bounding box; `filter_and_rank_drivers`
then applied an exact haversine radius. **Geographic proximity was the only locality
constraint.** `drivers.service_area_id` existed but was used solely for Spinr Pass
subscription/quota enforcement, document requirements, heatmap scoping and support-ticket
routing — never as a dispatch constraint. So any driver inside the ~10 km search box was a
candidate regardless of which city they were licensed for.

The ride's own `service_area_id` was already resolved at booking from the pickup point
(`routes/rides/booking.py`), so the data needed for the check was present and unused.

## 3. Fix / remediation

Restrict dispatch candidates to drivers whose approved service area is compatible with the
ride's pickup area, enforced at three layers (mirroring how the Spinr Pass gate is layered):

1. **SQL filter** on both the primary and vehicle-cascade candidate queries — keeps the
   candidate fetch small.
2. **In-Python re-check** in `filter_and_rank_drivers` — so a pool assembled any other way
   (a future RPC, a cached list, a hand-built fixture) is still gated. `is_wav` is
   double-checked the same way, for the same reason.
3. **Accept-time gate** in `routes/drivers/ride_flow.py` — a stale `ride_offers` row, a
   dispatch that ran with the flag off, or a driver calling the endpoint with a `ride_id`
   learned another way must not be able to complete the accept.

"Compatible" means *the same service-area tree*: the resolver finds the root by following
`parent_service_area_id` upward, then takes every descendant. This makes the relation
symmetric and transitive — a `regina` driver serves `regina_airport` rides, a
`regina_airport` driver serves `regina` rides, and two sub-areas of Regina serve each
other. A first draft used "self + parent + direct children", which silently excluded the
sibling case.

Both behaviours are governed by `app_settings` (DB-backed, flips without redeploy).

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend dispatch), but the highest-traffic path on it.**

The most serious risk in this change is **not** the cross-area block — it is the NULL
handling, and it was a real defect in the first iteration of this fix:

- `drivers.service_area_id` is `TEXT` **nullable, no default**, documented "assigned area
  (optional)" (`supabase_schema.sql:80`).
- **No migration backfills it** (checked every file in `backend/migrations/`).
- `routes/auth.py` never sets it — the signup path has zero references.
- The self-serve driver-row auto-create in `routes/drivers/profile.py` does not set it.
- `routes/admin/drivers.py:884` sets it only when supplied
  (`**({"service_area_id": ...} if service_area_id else {})`).
- Only the CSV importer (`services/driver_import_service.py:659`) reliably sets it.

SQL `IN (...)` never matches NULL. A bare `service_area_id IN (...)` predicate therefore
drops **every driver whose area was never assigned** — plausibly most of the fleet — giving
zero candidates, a 10 s retry chain, and a stuck-ride-sweeper cancellation ~5 minutes later,
for every ride in every area. That is strictly worse than the one-extra-offer bug being
fixed. Hence `service_area_allow_unassigned_drivers` defaults to **True** and the SQL
predicate is `service_area_id IN (...) OR service_area_id IS NULL`.

**Live NULL count — now VERIFIED, 2026-08-13:**

```
SELECT count(*) FILTER (WHERE service_area_id IS NULL) AS unassigned,
       count(*) AS total
FROM drivers WHERE deleted_at IS NULL AND status = 'active';
--  unassigned | total
--           0 |   153
```

All 153 active drivers have an area assigned. Consequences:

1. The already-merged `8a95c81` is **not** currently stranding rides — there is no
   NULL cohort for its bare `IN` to drop. Escaped by data, not by design.
2. With 0 NULL rows, `service_area_allow_unassigned_drivers=true` (the default
   shipped here) is **behaviourally identical to lockdown today** — the
   `IS NULL` leaf matches nothing. It costs nothing and protects against
   re-introduction.
3. NULL **can come back**: `routes/auth.py` never sets the column, the self-serve
   driver auto-create in `routes/drivers/profile.py` does not set it, and
   `routes/admin/drivers.py:884` sets it only when supplied. A driver approved
   without an area would be NULL + active + verified. See §12 for the durable fix.

Other consumers of the same fields/paths, all checked:

| Consumer | Reads | Affected? |
|---|---|---|
| `services/dispatch_service.py` `find_candidate_drivers` | `drivers.service_area_id` | Yes — same guard added, shares one helper so the two cannot drift |
| Spinr Pass subscription filter (`matching.py`) | ride `service_area_id`, `driver_subscriptions` | No logic change; runs on the already-narrowed pool |
| Daily quota filter (`matching.py`) | ride `service_area_id` | No logic change |
| Vehicle cascade (`matching.py`) | area `vehicle_cascade_map` | Yes — guard applied to the cascade pool too, else cascade bypasses it |
| `routes/drivers/status.py` go-online gates | driver `service_area_id` | Unchanged |
| `routes/drivers/profile.py` heatmap, `subscriptions.py`, `earnings.py`, `referrals.py`, `services/zoho_ticket_service_area.py` | driver `service_area_id` | Read-only, unchanged |
| Background loops (`core/lifespan.py`) | — | None touched |
| Ride state machine / money / wallet | — | Untouched; no transition, fare, or wallet delta changed |

**Self-edit escalation — checked, already mitigated.** `service_area_id` is user-writable via
`PUT /drivers/me`, which would otherwise let a driver reassign themselves into another city.
It sits in `vehicle_fields`, so changing it on an `active` driver forces
`status="needs_review"`, `is_online=False`, `is_available=False` (`profile.py:251-256`), and
dispatch requires `status="active"`. A driver cannot self-promote into another market.
Residual (not addressed here): a `pending` driver can set any area before approval, so the
admin approval screen should display the requested area.

**Interaction with the 500-row candidate cap:** the guard narrows the SQL result set, so it
can only reduce truncation pressure, never increase it.

**Deliberate choice — inactive areas stay compatible.** A driver assigned to an area an admin
just deactivated is still an approved driver for that city; dropping them would knock them
offline mid-shift with no signal.

## 5. User-experience effect

- **Driver:** an out-of-area driver stops receiving offers they were never authorised to
  take. Visible mid-session — a driver online right now in a city they are not approved for
  will go from receiving offers to receiving none. That is the intended correction, but it is
  a live behaviour change for anyone currently in that state. If they somehow still reach
  accept, they get a 403: *"This ride is outside your approved service area. Contact support
  if you have been approved to drive here."* — specific, non-technical, and actionable
  (points at support, which is the real remedy since approval is a manual process).
- **Rider:** none in the normal case. Where the only nearby driver was out-of-area, the
  rider now waits longer or gets no match instead of being assigned an unauthorised driver.
  That is the correct trade — an unauthorised driver is an insurance and regulatory exposure,
  not a successful match.
- **Corporate admin / internal admin:** no change.
- New copy: one driver-facing 403 message (above). No notification copy changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/service_area_scope.py` | **New.** Owns area-tree resolution, the SQL filter fragment, the per-driver predicate, and flag reading | One definition shared by both dispatch paths so they cannot drift |
| `backend/routes/rides/matching.py` | Area filter on the primary and cascade candidate queries; scope passed into both `filter_and_rank_drivers` calls; `service_area_id` added to projected columns | Primary dispatch path — where the reported bug occurred |
| `backend/services/dispatch_service.py` | Same guard in `find_candidate_drivers`; area check added to `filter_and_rank_drivers` (keyword-only, defaults to no-op); `app_settings` kwarg | Service-layer twin of the dispatch path |
| `backend/routes/drivers/ride_flow.py` | Accept-time area gate (403), fails closed | Backstop; matches the subscription gate's layering |
| `backend/schemas.py` | `enforce_driver_service_area`, `service_area_allow_unassigned_drivers` | Kill switch + NULL dual-read window, no redeploy |
| `backend/tests/test_cross_service_area_dispatch.py` | **New**, 24 tests | Guard behaviour incl. the reported scenario and the NULL regression |
| `backend/tests/test_accept_ride_service_area_gate.py` | **New**, 8 tests | Route-level accept gate against the real `accept_ride` |
| `backend/tests/services/test_dispatch_service.py` | Updated one test's expectations | `find_candidate_drivers` now reads `service_areas` |
| `backend/tests/test_dispatch_cascade.py`, `test_dispatch_match_attempt_branches.py`, `test_rides_matching_coverage.py` | Converted positional `side_effect` lists to table-keyed fakes; assertions located by intent instead of call index | See note below |

**Note on the four test-fixture conversions.** These were *pre-existing brittleness*, not
churn: they mocked `get_rows` with ordered `side_effect=[...]` lists and asserted on
`call_args_list[1]`. Adding one query anywhere on the dispatch path silently mis-assigned
every value — in `test_dispatch_cascade` the SUV pool received the XL driver row, so the pool
was non-empty, cascade never fired, and the test reported a *cascade* failure with no hint
that the cause was call ordering. Five tests failed this way. They now match on table and on
intent, so a future query addition cannot produce the same false failure.

## 7. Before / after

```python
# Before — routes/rides/matching.py: locality enforced by geography alone
_dispatch_filter: dict = {
    "is_online": True,
    "is_available": True,
    "is_verified": True,
    "status": "active",
    "vehicle_type_id": ride["vehicle_type_id"],
    "$and": dispatch_geo_bounds(_box_lat, _box_lng, search_radius),
}
if ride.get("requires_wav"):
    _dispatch_filter["is_wav"] = True
# → any driver inside the box is a candidate, whatever city they are approved for
```

```python
# After — approval is also required
if ride.get("requires_wav"):
    _dispatch_filter["is_wav"] = True

_area_ids, _allow_unassigned_area = await resolve_dispatch_area_scope(
    _deps.db_supabase, ride.get("service_area_id"), app_settings
)
if _area_ids is not None:
    _dispatch_filter.update(
        build_driver_area_filter(_area_ids, allow_unassigned=_allow_unassigned_area)
    )
# build_driver_area_filter emits, when unassigned drivers are allowed (the default):
#   {"$or": [{"service_area_id": {"$in": [...]}}, {"service_area_id": None}]}
# The IS NULL leaf is load-bearing — SQL IN never matches NULL, and the column is
# unpopulated for drivers who signed up in-app.
```

## 8. Rollback plan

**No redeploy required.** Two `app_settings` values, both live within the 60 s settings cache:

| Symptom | Action |
|---|---|
| Rides failing to match / drivers report no offers | Set `enforce_driver_service_area = false` → dispatch reverts to exactly the pre-guard proximity-only behaviour, and the accept gate is bypassed |
| Only *unassigned* drivers affected (if lockdown was enabled prematurely) | Set `service_area_allow_unassigned_drivers = true` |

No migration, no schema change, no data mutation — nothing to unwind. No ride state, wallet
delta, Stripe object, or insurance-period row is written by this change, so a code revert is
also safe on its own if the flag route is unavailable.

## 9. Verification performed

- [x] **Full backend suite: 11 098 passed, 8 skipped, 1 xfailed, 0 failed** (`pytest tests/`).
      First run surfaced 5 real failures (the fixture-ordering issue in §6); all fixed and
      re-run clean.
- [x] New tests: 24 in `test_cross_service_area_dispatch.py`, 8 in
      `test_accept_ride_service_area_gate.py` — unit + route-level.
- [x] **Mutation-tested both guards.** Neutralising `build_driver_area_filter` /
      `driver_area_allowed` fails 9 tests including the headline
      `test_saskatoon_driver_does_not_receive_regina_ride`; disabling the accept gate fails
      2. This was done because the *first* version of these tests was a tautology — it mocked
      `get_rows` to return `[]` and then asserted the result was `[]`, so it passed with the
      guard deleted. The suite now evaluates the real `$in` / `$or` predicate (including
      `NULL IN (...)` being false) against real driver rows.
- [x] Accept-gate allow-path assertions are non-vacuous: they assert the *quota gate* (the
      next statement after the area gate) was reached, so an exception raised before the gate
      cannot make them pass. Caught a genuine harness bug — `_deps.db` **is**
      `_deps.db_supabase`, so patching `_deps.db.find_one` was silently replacing the
      `db_supabase.find_one` mock.
- [x] Blast-radius grep: `service_area_id` across `backend/services/`, `backend/routes/`,
      `backend/migrations/`, `supabase_schema.sql`; `filter_and_rank_drivers` and
      `find_candidate_drivers` call sites; driver-creation paths in `routes/auth.py`,
      `routes/drivers/profile.py`, `routes/admin/drivers.py`, `services/driver_import_service.py`.
- [x] `ruff check` + `ruff format` clean on all 11 touched files. (26 pre-existing lint
      errors elsewhere in `tests/` were left alone — none in files touched here.)
- [x] Conventions reviewed: ride state machine (no transition changed), dual-import pattern
      followed in the new module, `logger.error` + `exc_info` on DB faults, no GPS/PII in
      logs (area IDs and counts only), additive-over-destructive (new flags, no column
      repurposed), feature-flagged, no silent caps (truncation logged).
- [x] Fail-safe posture reviewed: on a `service_areas` read failure dispatch narrows to the
      ride's own area — cross-area drivers stay blocked, same-area dispatch continues —
      rather than failing fully open (would restore the bug) or fully closed (would strand
      every ride). The accept gate fails **closed** (503), matching the subscription gate.

## 10. What was NOT verified

- ~~The production NULL count~~ — **resolved, see §4.** 0 of 153 active drivers have a NULL
  area. Only the `active`/`deleted_at IS NULL` cohort was counted; drivers in other statuses
  (`pending`, `needs_review`, `suspended`) were not, and are irrelevant to dispatch because it
  requires `status='active'` and `is_verified=true`.
- **No staging run and no real Supabase.** Everything above is mocked/unit-level; PostgREST's
  actual `or=(service_area_id.in.(...),service_area_id.is.null)` compilation was verified by
  reading `repositories/_base.py` (`_build_or_clause_term` line 676 → `col.is.null`; `$or`
  and `$and` coexist per `_apply_filters` lines 702-719) and reproduced in a fake, **not**
  executed against Postgres. Worth one staging dispatch before merge.
- **No production build run.** Backend-only change; no `admin-dashboard` / `rider-app` /
  `driver-app` code touched, so no `npm run build` applies.
- **No load/latency measurement.** The guard adds exactly one `service_areas` read per
  dispatch attempt (down from three in the first draft — a per-level BFS). `service_areas` is
  small and already read in full per request by `booking.py` and `estimates.py`, so the P95
  < 2 s dispatch SLA is expected to hold, but this was reasoned about, not measured. Note the
  same row is now read by `resolve_matching_config`, the subscription filter, the cascade
  block and the polygon fetch — pre-existing duplication this change does not fix.
- **The one-area-per-driver data model was not changed.** `drivers.service_area_id` is a
  single value, so a driver legitimately approved for two cities cannot be represented; they
  would need one area set and would be blocked in the other. Whether that case exists is a
  product question, flagged not answered.
- **No visual/snapshot regression tooling exists for this surface** (backend), so the
  driver-facing 403 copy was reviewed by reading, not rendered.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (two `app_settings` booleans, no redeploy)
- [x] Blast radius stated with each consumer named, not assumed
- [x] Live-visible behaviour change documented in §5 (an out-of-area driver online right now
      stops receiving offers)
- [x] Production NULL count confirmed: 0 of 153 active drivers (§4)
- [ ] **Recommended before merge:** one staging dispatch to confirm the PostgREST `$or`
      compiles as expected against real Postgres

## 12. Follow-ups this change does NOT cover

1. **`drivers.service_area_id` can still become NULL.** The column is not `NOT NULL`, and
   neither signup nor admin approval requires it. The durable fix is to make the area
   mandatory at the point of approval (`routes/admin/drivers.py`) and then add a `NOT NULL`
   constraint via migration — at which point both flags here become dead code and can be
   removed. Until then the `IS NULL` leaf is the safety net.
2. **Whether to enable lockdown now.** With 0 NULL rows,
   `service_area_allow_unassigned_drivers = false` is a no-op today and would permanently
   close the loophole for any future NULL row. The trade-off: if an admin later approves a
   driver without an area, that driver silently receives no offers instead of receiving
   offers everywhere. Failing closed is the right posture for a regulatory gate, but it is a
   live-dispatch policy decision, so the default was left at `true` rather than changed
   unilaterally — flip it once (1) is in place.
3. **One area per driver.** `drivers.service_area_id` is a single value, so a driver
   legitimately approved for two cities cannot be represented and would be blocked in one of
   them. No such driver is known to exist; flagged, not solved.
4. **Duplicate `service_areas` reads on the dispatch path** — `resolve_matching_config`, the
   subscription filter, the cascade block, the polygon fetch and now the area scope each read
   the same row. Pre-existing; this change adds one read and removes none.
