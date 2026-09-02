# Domain — Dispatch

_Load when working on: driver matching, offer timeouts, ride search, location updates, WebSocket dispatch events._

## Key files

- `backend/services/dispatch_service.py` — matching algorithm entry point
- `backend/routes/rides/` — `/rides/request`, `/rides/{id}/accept`, offer-timeout logic (see `matching.py`, `booking.py`, `lifecycle.py`)
- `backend/routes/drivers/` — `go_online`, `go_offline`, location batch update (see `status.py`, `location.py`)
- `backend/socket_manager.py` + `backend/utils/ws_pubsub.py` — WS fan-out
- `backend/core/lifespan.py` — scheduled-dispatch background loop

## Matching algorithm (current)

1. Rider requests → ride inserted with `status='searching'`
2. Dispatch service queries online drivers within radius (expanding: 2 km → 5 km → 10 km)
3. Rank by: ETA (weight 0.6) + driver rating (0.2) + acceptance rate (0.2)
4. Send offer to top driver → update ride to `driver_assigned`, start 15 s timeout
5. Driver accepts → `driver_accepted` + WS event to rider
6. Driver ignores/declines → release, loop step 3 with next driver
7. No drivers after ~5 minutes → auto-cancel with `ride_cancelled` WS event

## Candidate fetch (step 2) — two paths, one predicate

`routes/rides/matching.py` fetches the candidate pool twice per dispatch attempt
(the primary pool, and the vehicle-type cascade pool). Both go through
`_fetch_candidate_drivers`, which picks a path from `DISPATCH_SPATIAL_CANDIDATES`:

| Flag | Query | Behaviour at the 500 cap |
|---|---|---|
| unset (**default today**) | `get_rows("drivers", …)` with a lat/lng bounding box from `dispatch_geo_bounds` | Arbitrary slice — the nearest driver can be dropped, producing a false "no drivers" |
| set | `dispatch_candidate_drivers` RPC (migration 395) over the `location_geog` GiST index, KNN-ordered | The 500 **nearest** are kept |

Rules when touching either path:

- **Both must over-fetch by the same margin.** `_padded_radius_km()` (10% + 1 km)
  is shared for exactly this reason. `filter_and_rank_drivers` is the exact
  distance gate; a tighter fetch silently shrinks the pool it ranks, with no
  error anywhere.
- **Nothing else moves into SQL.** Ranking, the presence filter, the subscription
  gate and the service-area guard all stay in Python, after the fetch.
- **The RPC's predicate and the filter dict are one predicate written twice.**
  Edit one, edit the other, and update the parity test
  (`tests/test_dispatch_spatial_candidates.py`), which compares them branch by
  branch. Two traps it exists to catch: a WAV driver must still receive non-WAV
  offers, and SQL `= ANY` never matches NULL, so a driver with no
  `service_area_id` needs its own arm or vanishes from dispatch.
- **The RPC's `RETURNS TABLE` types must match `drivers` exactly.**
  `rating` is `FLOAT`, `destination_mode` is `BOOLEAN`. Casting the latter to
  text hands Python the string `'false'`, which is truthy — every driver then
  looks like they are in destination mode.
- An RPC failure falls back to the box query and increments
  `spinr_dispatch_spatial_fallback_total`. A fallback *failure* raises, so a DB
  error reaches the retry shell instead of looking like an empty pool.

Status: flag off everywhere. See `docs/change-log/2026-09-02-spatial-dispatch-candidates.md`
§11 for the staging checks required before enabling it.

## Offer timeout

- Timeout handler filters on `status='driver_assigned' AND driver_id=<current>` — atomic
- On timeout: driver released (removed from assignment), ride returns to `searching`
- Never re-offer to a driver who already declined this ride in this search cycle

## Race conditions to guard

- **Two drivers accepting** — Supabase update filters `{'status': 'searching'}`; 0 rows → 409 + `ride_taken` WS event
- **Driver going offline mid-offer** — Check `drivers.status == 'online'` at acceptance time
- **Rider cancelling during offer** — Offer handler must re-read ride state; if `cancelled`, skip driver notification

## WS events (dispatch domain)

| Event | Direction | Payload |
|---|---|---|
| `ride_requested` | backend → rider | ride_id, estimated_wait |
| `driver_assigned` | backend → rider | ride_id, driver_id, eta, vehicle |
| `driver_accepted` | backend → rider | ride_id, driver location |
| `ride_taken` | backend → driver | ride_id (409 equivalent) |
| `ride_cancelled` | backend → both | ride_id, reason, is_auto |
| `offer` | backend → driver | ride_id, pickup, dropoff, fare, timeout |

## Performance targets

- P95 offer → driver phone notification: < 2 s
- P95 acceptance → rider WS event: < 500 ms
- Matching query: < 300 ms against 1k online drivers

## Common pitfalls

- Don't batch multiple rider WS events into one message — fragile on client
- Don't assume driver location freshness > 30 s
- Don't count a driver as "available" if their last heartbeat is > 90 s old
- Don't run the scheduled-dispatch loop faster than 60 s — Supabase quota
