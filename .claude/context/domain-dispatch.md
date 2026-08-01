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
