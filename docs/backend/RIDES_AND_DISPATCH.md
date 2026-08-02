# Rides, Dispatch & Drivers Domain

The ride lifecycle, driver operations, fare pricing, and realtime transport layer.

**Files covered:**
`routes/rides.py`, `routes/drivers.py`, `routes/fares.py`, `routes/fare_split.py`, `routes/websocket.py`, `routes/admin/drivers.py`, `routes/admin/rides.py`, `services/dispatch_service.py`, `services/fare_service.py`, `socket_manager.py`, `utils/ws_pubsub.py`, `utils/surge_engine.py`, `utils/scheduled_rides.py`, `utils/document_expiry.py`, `geo_utils.py`.

---

## 1. Domain concepts

- **Ride** — one transportation request from a rider, containing pickup, dropoff, optional stops, pricing breakdown, status, and realtime timeline.
- **Driver** — `drivers` row linked 1:1 to a `users` row. Carries vehicle info, verification status, live location, availability, and earnings.
- **Dispatch** — process of picking a driver for a newly-searching ride, notifying them, and handling accept/decline/timeout.
- **Service area** — polygon + pricing + matching config. Ride pricing and dispatch rules are resolved per service area.
- **Surge** — demand/supply multiplier per service area, updated every 2 min.
- **Offer timeout** — driver-side countdown (default 15 s client + 15 s server grace). Server re-dispatches if no accept.

---

## 2. Ride state machine

```
             ┌─────────────────────────────────────────────────────┐
             ▼                                                     │
   ┌────────────────┐     (driver claim succeeds)                  │
   │   searching    ├──────────────────────────────┐               │
   └────────┬───────┘                              │               │
            │                                      ▼               │
            │                            ┌────────────────┐        │
            │                            │ driver_assigned│        │
            │                            └────────┬───────┘        │
            │                 (30 s offer timeout)│                │
            │    ◄─────────────────────────────── │                │
            │       (driver declines)             ▼                │
            │                            ┌────────────────┐        │
            │                            │driver_accepted │        │
            │                            └────────┬───────┘        │
            │    (driver cancels mid-trip)        │                │
            │                                     ▼                │
            │                            ┌────────────────┐        │
            │                            │ driver_arrived │        │
            │                            └────────┬───────┘        │
            │    (any point)                      │                │
            │                                     ▼                │
            │                            ┌────────────────┐        │
            │                            │  in_progress   │        │
            │                            └────────┬───────┘        │
            │                                     │                │
            │                                     ▼                │
            │                            ┌────────────────┐        │
            │                            │   completed    │        │
            │                            └────────────────┘        │
            │                                                      │
            ▼                                                      │
   ┌────────────────┐                    ┌────────────────┐        │
   │no_driver_found │  (5 min timeout)   │   cancelled    │◄───────┘
   └────────────────┘                    └────────────────┘
                              (rider cancel, admin cancel, driver cancel)
```

### Transition guards (`drivers.py`)

```python
ARRIVE_FROM_STATES   = ("driver_assigned", "driver_accepted", "driver_arrived")
START_FROM_STATES    = ("driver_arrived", "in_progress")
COMPLETE_FROM_STATES = ("in_progress",)
```

Helper `_require_ride_in_state(ride_id, driver_id, allowed_states)` loads the ride, returns 409 on wrong state, 404 if missing. Idempotent transitions (e.g. `/arrive` when already `driver_arrived`) succeed.

### Free-cancel window

Rider cancel within 2 minutes of `driver_accepted_at` is free; afterwards a $3 fee is charged. See `cancel_ride_rider` in `routes/rides.py`.

---

## 3. Dispatch flow

```
POST /rides  → create_ride()
   ├─ validate, resolve service area, compute fares
   ├─ INSERT ride (status = searching)
   └─ asyncio.create_task(match_driver_to_ride(ride_id, ride=fresh_row))
         │
         ▼
   match_driver_to_ride()
   ├─ resolve_matching_config(ride):
   │     algorithm ∈ {nearest, rating_based, round_robin, combined}
   │     min_rating, search_radius_km  (service-area override → app_settings)
   ├─ find_candidate_drivers:
   │     SELECT drivers WHERE is_online AND is_available AND vehicle_type_id = ride.vehicle_type_id LIMIT 500
   ├─ filter_and_rank_drivers:
   │     drop orphans (no user_id or no lat/lng)
   │     filter by rating + haversine distance
   │     attach distance_km
   ├─ select_driver_by_algorithm(…):
   │     nearest      → sort by distance
   │     rating_based → sort by rating desc
   │     combined     → rating gate then nearest
   │     round_robin  → pick next after last_assigned_driver_id
   ├─ claim_any_driver(ranked):
   │     try drivers in order; claim_driver_atomic flips is_available=false ONLY IF still true
   │     first successful claim wins
   ├─ UPDATE ride SET driver_id, status=driver_assigned, driver_notified_at
   ├─ WS → rider  {type: driver_assigned}
   ├─ WS + FCM → driver  {type: new_ride_assignment, pickup, dropoff, fare, …}
   └─ schedule _offer_timeout_handler(ride_id, driver_id, rider_id, timeout=30s)
```

**Offer timeout handler:**

```
sleep(timeout_seconds)
load ride
if ride.status == driver_assigned AND ride.driver_id == this_driver:
   UPDATE ride SET driver_id=NULL, status=searching
   free driver (is_available = true)
   WS → rider  {type: still_searching}
   re-dispatch: match_driver_to_ride(ride_id)
```

**Search timeout** (`ride_search_timeout`): if the ride is still `searching` 5 min after create, auto-cancel → `no_driver_found`; notify rider.

**Race safety.** `claim_driver_atomic` is the single serialization point — two dispatchers racing on the same candidate see exactly one `True`. Followed by a re-read to confirm the update persisted (guards against phantom state on retries).

### Dispatcher algorithms

`services/dispatch_service.py:84 select_driver_by_algorithm`:

| Algorithm | Tie-breaker |
|---|---|
| `nearest` | smallest distance |
| `rating_based` | highest rating |
| `combined` | apply rating gate, then nearest |
| `round_robin` | next in circular order after `last_assigned_driver_id` |

Pure functions — easy to unit-test without a DB.

---

## 4. Fare pricing

### Estimate / create path

```
POST /estimate  or  POST /rides
  ├─ distance = haversine(pickup, dropoff)
  ├─ duration = distance / 30 km/h + 5 min     (heuristic; refined at complete)
  ├─ fares_for_location(lat, lng):
  │     ├─ fetch active vehicle_types
  │     ├─ fetch active service_areas → find polygon containing pickup
  │     ├─ if no match → _build_default_fares (base $3.50, per-km $1.50, per-min $0.25, min $8, booking $2)
  │     └─ else:
  │           surge = area.surge_multiplier (or 1.0)
  │           fetch fare_configs for (service_area, is_active)
  │           join with vehicle_types
  │           apply surge via Decimal; _fd() quantizes to 2 dp
  ├─ per vehicle_type:
  │     distance_fare = round(per_km_rate · distance · surge, 2dp)
  │     time_fare     = round(per_min_rate · duration · surge, 2dp)
  │     subtotal      = base + distance + time + booking + airport_fee
  │     total         = max(subtotal, minimum_fare)
  └─ response includes {vehicle_type, total_fare, eta_minutes, driver_count, available}

On /rides additionally:
  ├─ airport fee detection (ray-cast pickup/dropoff/stops against airport polygons)
  ├─ area_fees_breakdown (per-area flat/percent)
  ├─ tax_amount         (tax_rate × (total_fare + area_fees_total))
  ├─ grand_total        = total_fare + area_fees_total + tax_amount
  ├─ driver_earnings    = base + distance + time
  └─ admin_earnings     = booking + airport_fee + area_fees + tax (platform cut)
```

### Cache

`GET /fares?lat=…&lng=…` uses Redis with key `fares:{round(lat,2)}:{round(lng,2)}` — ~1.1 km grid cell, 5-min TTL (`FARE_CACHE_TTL_SECONDS`). `invalidate_fare_cache()` deletes the `fares:*` pattern after service-area or fare-config updates.

### Complete-time recalculation

`POST /drivers/rides/{id}/complete` (drivers.py:1719):

```
aggregate driver_location_history for this ride
compute phase_distances:
   navigating_to_pickup | trip_in_progress | arrived_at_pickup | online_idle
actual = phase_distances["trip_in_progress"]
if |actual - planned| > 0.1 km:
    per_km_rate = old_distance_fare / planned_distance
    new_distance_fare = round(per_km_rate · actual, 2dp)
    recompute total_fare, driver_earnings
UPDATE ride SET actual_distance_km, phase_distances, route_polyline (≤200 pts), gps_points_count, fares
free driver; increment driver.total_rides
```

### Precision

Every monetary hop goes `float → Decimal → quantize(0.01, ROUND_HALF_UP) → float` via `_fd()` / `_d()` / `_round()` / `_f()`. Prevents IEEE-754 drift accumulating across hundreds of rides. Also `fare_service.merge_fare_configs_with_vehicle_types` applies Decimal at the join so downstream rides.py starts clean.

---

## 5. Surge engine (`utils/surge_engine.py`)

Every 2 min for each **non-manual** active service area:

```
demand = count(rides in last 10 min, status ∈ searching|driver_assigned|driver_en_route, area)
supply = count(drivers online+available within area polygon)
ratio  = demand / max(supply, 1)

ratio < 0.5   → 1.00×
       < 0.8 → 1.25×
       < 1.2 → 1.50×
       < 2.0 → 1.75×
       < 3.0 → 2.00×
      ≥ 3.0  → 2.50× (cap)

if multiplier changed:
    UPDATE service_areas.surge_multiplier
```

Admin manual override (`surge_source='manual'`) is preserved. Sub-areas (airports) are skipped and managed at their parent level.

---

## 6. Scheduled rides (`utils/scheduled_rides.py`)

Loop every 60 s (±6 s jitter, so replicas don't all wake on the same tick boundary):

```
for ride in (is_scheduled=true, status='scheduled'):   # 'scheduled' ONLY — see CR-2 below
  if now within 10 min of scheduled_time and not reminder_sent:
      push reminder; mark reminder_sent=true
  if now >= scheduled_time and not scheduled_dispatched:
      atomic claim: UPDATE rides SET status='searching', scheduled_dispatched=true
                    WHERE id=? AND status='scheduled'   # 0 rows = another replica already won
      await match_driver_to_ride(ride_id)
      push "Your scheduled ride is starting!"
```

Idempotent via two flags (`reminder_sent`, `scheduled_dispatched`), plus an atomic
`status='scheduled'`-filtered claim so only one replica/tick can win the dispatch race.

**CR-2**: the query used to also match `status='searching'`, which meant a
correctly-parked scheduled ride was invisible to this loop once dispatched by
another path and never re-checked. Fixed to filter on `status='scheduled'`
only; regression-tested in `test_check_queries_scheduled_status`. Do not
reintroduce the wider filter.

If the rider already has another active ride when the scheduled pickup time
arrives, the claim fails on the `rides_one_active_per_rider` constraint and
the ride is deferred to a later tick rather than dispatched.

---

## 7. Document expiry (`utils/document_expiry.py`)

Loop every 12 h. For each driver, check legacy columns (`license_expiry_date`, `insurance_expiry_date`, `vehicle_inspection_expiry_date`, `background_check_expiry_date`, `work_eligibility_expiry_date`) plus `driver_documents.expires_at`. If any is in `(0, 7]` days and the driver has not been warned in the last 24 h (`doc_expiry_warned_at`), push a notification with the soonest expiry. Dedupe via timestamp.

---

## 8. Rider endpoints (`routes/rides.py`)

Representative set — see `REFERENCE.md` for full inventory.

| Method | Path | Purpose |
|---|---|---|
| POST | `/rides/estimate` | Per-vehicle fare estimates + availability. |
| POST | `/rides` | Create ride, spawn dispatch, schedule 5-min auto-cancel. |
| GET | `/rides/active` | Current active ride or unpaid completed (resume flow). |
| GET | `/rides/history` | Completed + cancelled-with-driver (no orphan searching rides). |
| GET | `/rides/{id}` | Details; riders see minimal driver info only (no PII). |
| POST | `/rides/{id}/tip` | Add tip to completed ride; increments driver_earnings. |
| POST | `/rides/{id}/process-payment` | Idempotent charge (wallet debit or card stub). |
| POST | `/rides/{id}/rate` | Rating + comment on completed ride. |
| POST | `/rides/{id}/cancel` | Free if <2 min after accept, else $3 fee. |
| GET / POST | `/rides/{id}/share` | Shareable tracking link + notify contact. |
| GET | `/rides/track/{share_token}` | Public tracking page, no auth. |
| POST | `/rides/{id}/stops` | Add waypoint mid-trip. |
| DELETE | `/rides/{id}/stops/{idx}` | Remove stop. |
| POST | `/rides/{id}/emergency` | SOS (support / emergency contact / LEO). |
| GET / POST | `/rides/{id}/messages` | In-ride chat, persisted + forwarded via WS. |
| GET | `/rides/{id}/call` | Twilio call token. |
| GET / DELETE | `/rides/scheduled[/{id}]` | Manage scheduled rides. |
| GET | `/rides/{id}/receipt` | Email-ready receipt. |

Dev endpoints (`simulate-arrival`, rider-side `start` / `complete`) exist for testing and flow configs where the driver-OTP path is bypassed.

---

## 9. Driver endpoints (`routes/drivers.py`)

### Onboarding & operations

| Method | Path | Purpose |
|---|---|---|
| GET | `/drivers/me` | Current driver profile. |
| PUT | `/drivers/me` | Update profile; vehicle edits re-trigger review. |
| POST | `/drivers/register` | Become-driver: upsert driver row, flip user role. |
| POST | `/drivers/push-token` | FCM token registration. |
| POST | `/drivers/status` | Toggle online / available; frees driver when going offline. |
| GET/POST/DELETE | `/drivers/destination` | Destination mode (work towards a location). |
| GET | `/drivers/demand-heatmap` | Recent pickups in driver's service area. |
| GET | `/drivers/config` | Runtime config (offer timeout, pickup radius). |

### Ride actions

| Method | Path | Guard |
|---|---|---|
| GET | `/drivers/rides/active` | — |
| GET | `/drivers/rides/history` | — |
| POST | `/drivers/rides/{id}/accept` | status == driver_assigned |
| POST | `/drivers/rides/{id}/decline` | status == driver_assigned |
| POST | `/drivers/rides/{id}/arrive` | status ∈ ARRIVE_FROM_STATES; geofence ≤ 200 m |
| POST | `/drivers/rides/{id}/verify-otp` | status ∈ START_FROM_STATES |
| POST | `/drivers/rides/{id}/start` | status ∈ START_FROM_STATES |
| POST | `/drivers/rides/{id}/complete` | status ∈ COMPLETE_FROM_STATES |
| POST | `/drivers/rides/{id}/cancel` | driver cancels mid-trip |

### Earnings & payouts

`balance`, `earnings`, `earnings/daily | trips | weekly | monthly | comparison`, `earnings/export` (CSV), `t4a/{year}` (Canadian tax summary), `stripe-onboard` (Connect URL), `bank-account` (GET/POST/DELETE), `payouts` (request + history).

### Location upload

`POST /drivers/location-batch` — for offline recovery. Accepts single dict or list of GPS points; server tags each point with the right `tracking_phase` based on any active ride's status.

---

## 10. Realtime layer

### WebSocket connect + auth (`routes/websocket.py`)

```
Client → WS /ws/{client_type}/{client_id}
server accept
client → {type:"auth", token}
server verifies Firebase ID token; falls back to legacy JWT
server → {type:"auth_success", client_type}
spawn 30-s ping heartbeat
loop: handle messages (rate limited 30 msg/s, max 64 KB)
```

`connection_key = "{client_type}_{user_id}"` — e.g. `driver_abc123`, `rider_xyz`, `admin_admin1`.

### Fan-out (`utils/ws_pubsub.py`)

Single shared channel `spinr:ws:dispatch`. Every replica subscribes. Sender publishes `{client_id, message}`; every replica's consumer delivers locally iff the target is connected to that replica. Without pub/sub active in a multi-replica deploy, ~50% of messages are silently dropped — `WS_REDIS_URL` is required in prod.

Local-only fallback: dev without Redis works fine single-replica. `ws_pubsub.start()` returns False and senders fall through to direct delivery.

### Event types

**Client → server**: `auth`, `pong`, `driver_location` / `location_update`, `location_batch`, `chat_message`, `get_nearby_drivers`, `ride_status_update`.

**Server → client**: `auth_success`, `ping`, `driver_assigned`, `new_ride_assignment`, `driver_accepted`, `driver_arrived`, `ride_started`, `ride_completed`, `ride_cancelled`, `driver_location_update`, `driver_status_changed`, `ride_status_changed`, `chat_message`, `nearby_drivers`, `location_batch_ack`, `error`.

### GPS path

Driver publishes `driver_location` → server (a) updates `socket_manager.driver_locations` in-memory, (b) inserts a row into `driver_location_history` with `tracking_phase` derived from any active ride's status, (c) updates `drivers.lat,lng`, (d) publishes `driver_location_update` to the rider on that ride + all admin clients via Redis.

Phase tags: `navigating_to_pickup`, `arrived_at_pickup`, `trip_in_progress`, `online_idle`.

---

## 11. Fare split (`routes/fare_split.py`)

```
POST /fare-split
  ├─ split_count = len(participant_phones) + 1
  ├─ per_person_share = total_fare / split_count  (Decimal)
  ├─ INSERT fare_splits
  └─ INSERT fare_split_participants (status='pending') per phone

GET /fare-split/{id}            — split + participants with share
GET /fare-split/ride/{ride_id}  — does this ride have an active split?
POST /fare-split/{id}/respond   — accept | decline (participant)
POST /fare-split/{id}/pay       — wallet debit or card charge
```

---

## 12. Admin surface

### Drivers (`routes/admin/drivers.py`)

List (deduped by phone+user_id), stats dashboard, update, verify, action (approve/reject/suspend/ban/unban/reactivate), status-override, notes (CRUD), activity log, rides, daily stats, assign service area, GPS location trail.

`_log_driver_activity(driver_id, event_type, title, description, metadata, actor)` writes to `driver_activity_log` from every lifecycle action.

### Rides (`routes/admin/rides.py`)

List, active (live monitoring), cancel (frees driver, notifies both), stats (overall + detailed by hour/day/vehicle), ride details, GPS trail, live data, invoice, route map PNG, heatmap, earnings (financial summary), exports (rides CSV/JSON, drivers CSV), payouts, payout stats.

### Monitoring (`routes/admin/monitoring.py`)

`GET /admin/monitoring/drivers` — all drivers with location, availability, on-ride flag.

`GET /admin/monitoring/rides` — active rides (searching/assigned/arrived/in_progress) with participants.

Feeds the ops live map.

### Maintenance (`routes/admin/maintenance.py`)

`POST /maintenance/cleanup-location-history?days=30` — purge old GPS + `online_idle` points >24 h old. Idempotent.

`POST /maintenance/rollup-driver-daily?target_date=…` — rollup per-driver daily stats (online_minutes, idle_km, navigating_km, trip_km, counts, earnings). Upsert keyed on (driver_id, stat_date).

---

## 13. Fares endpoints (`routes/fares.py`)

```
GET /vehicle-types          → List[VehicleType]
GET /fares?lat=&lng=        → List[fare-for-vehicle-type]  (Redis cached)
```

`get_fares_for_location` is the single public entry; `create_ride` reuses `_fares_for_location_impl` with pre-fetched `all_areas` + `vehicle_types` so it does not redundantly hit Supabase.

---

## 14. Geo utilities (`geo_utils.py`)

| Function | Algorithm | Use |
|---|---|---|
| `calculate_distance(lat1, lng1, lat2, lng2)` | Haversine | Distances between points in km. |
| `get_service_area_polygon(area)` | Array/GeoJSON parsing | Support legacy polygon lists + GeoJSON Polygon. |
| `point_in_polygon(lat, lng, polygon)` | Ray-cast even-odd | Service-area / airport-zone membership. |

---

## 15. Common tasks

| Task | Where |
|---|---|
| Change offer timeout | `drivers.get_driver_config` default + `app_settings.ride_offer_timeout_seconds`. |
| Change pickup geofence radius | `app_settings.pickup_radius_meters` (served via `/drivers/config`). |
| Add a new dispatch algorithm | `services/dispatch_service.py:select_driver_by_algorithm` + `resolve_matching_config`. Keep the selector pure. |
| Tune surge tiers | `utils/surge_engine.py:ratio_to_multiplier`. |
| Add a new ride status | Start with the transition map in this doc; update guard tuples in `drivers.py`; update `routes/admin/rides.py` filters; check WS consumers on mobile. |
| Add a new WS event | Define producer in handler; document in §10; consumers on rider / driver / admin apps. Publish via `manager.send_personal_message`. |
| Expand fare formula | `services/fare_service.py:merge_fare_configs_with_vehicle_types` and the per-vehicle loop in `routes/rides.py:create_ride`. Keep Decimal precision. |
