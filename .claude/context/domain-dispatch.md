# Domain — Dispatch

_Load when working on: driver matching, offer timeouts, ride search, location updates, WebSocket dispatch events._

## Key files

- `backend/services/dispatch_service.py` — matching algorithm entry point
- `backend/routes/rides/` — `/rides/request`, `/rides/{id}/accept`, offer-timeout logic (see `matching.py`, `booking.py`, `lifecycle.py`)
- `backend/routes/drivers/` — `go_online`, `go_offline`, location batch update (see `status.py`, `location.py`)
- `backend/socket_manager.py` + `backend/utils/ws_pubsub.py` — WS fan-out
- `backend/core/lifespan.py` — scheduled-dispatch background loop

## Matching algorithm (current — verified against code 2026-09-25)

Source: `routes/rides/matching.py` (`match_driver_to_ride`, `_dispatch_retry`,
`ride_search_timeout`), `services/dispatch_service.py` (`resolve_matching_config`).

1. Rider requests → ride inserted with `status='searching'`.
2. Candidates: available drivers within **one fixed radius** — `search_radius_km`
   (service area overrides global; default 10 km). There is **no** 2 → 5 → 10 km
   expansion today.
3. Ranking: `driver_matching_algorithm` (`nearest` default, `rating_based`,
   `combined`, `round_robin`), then Distance-Matrix ETA via
   `rank_by_eta_with_acceptance` (effective ETA = ETA ÷ acceptance rate, rate
   floored at 0.1). Falls back to haversine order if the ETA call fails or is slow.
4. **Batch offers**: the top `max_simultaneous_offers` drivers (area overrides
   global; default 3, range 1–10) are claimed and offered at once. Each offer
   lasts `ride_offer_timeout_seconds` (default 15 s, range 5–60).
5. First driver to accept wins (`driver_accepted` + WS event to rider); the
   other drivers in the batch get a `ride_taken` WS event.
6. No acceptance (all declined/expired) or no candidates → re-dispatch every
   10 s (`_dispatch_retry`).
7. On-demand ride still searching after `ride_search_timeout_seconds` (default
   300 s, clamped 90–300, migration 468) → auto-cancel `no_drivers_found` with a
   `ride_cancelled` WS event + push. Enforced by the in-process timer and by
   `utils/stuck_ride_sweeper.py` (60 s, restart-safe) reading the same setting.
   The retry cap is `ceil(window ÷ 10 s)` (30 at 300 s). Scheduled rides ignore
   this setting and keep a fixed 300 s grace after pickup.

## Offer timeout

- Timeout handler filters on `status='driver_assigned' AND driver_id=<current>` — atomic
- On timeout or decline: driver released, ride returns to `searching`
- The driver gets a `spinr:offer_skip:{ride}:{driver}` Redis key with a 300 s
  TTL, so they are not offered **this ride** again for 300 s. This is why the
  search window is capped at 300 s: `ride_offers` is `UNIQUE(ride_id, driver_id)`
  (migration 100), and a re-offer after the key expires fails the bulk insert.
- Flags that default **off** (settings migrations 466, 469; plan
  `.claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md`):
  `offer_expired_decline_as_miss_enabled` (countdown auto-decline counts as a
  miss) and `dispatch_expanded_radius_enabled` (+ `_after_seconds`,
  `_multiplier`, `_max_km`). Re-offering a ride to a driver who declined it is
  **not possible** while `ride_offers` keeps `UNIQUE(ride_id, driver_id)`.

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
