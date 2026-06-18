# ADR-008: Airport FIFO dispatch queue (per-venue, opt-in)

**Date:** 2026-06-17
**Status:** Proposed
**Relates to:** dispatch domain (`backend/services/dispatch_service.py`, `backend/routes/rides.py`), venues (migration 134)

---

## Context

Spinr's production dispatch ranks drivers by **nearest / ETA-weighted-by-acceptance**
(see `.claude/context/domain-dispatch.md`). This is correct for normal city
dispatch: it minimises rider wait and driver dead-miles.

It breaks down in one topology — high-density **staging zones** like the
Saskatoon (YXE) / Regina (YQR) airport cell-phone lots. There, 20–40 drivers sit
in one ~150 m lot, all effectively equidistant from the terminal. "Nearest"
degenerates into a near-random tiebreak, so a driver who has waited 90 minutes
watches a just-arrived driver get the pickup. Drivers perceive this as unfair,
jockey for position, and churn off the airport.

Every mature ride-share/taxi operation solves this with a **first-in-first-out
queue** at the airport: the longest-waiting eligible driver is offered first.
This is the one place where wait-time, not distance, is the fair ordering signal.

We want this **only** at staging zones — never as the global dispatch policy,
because FIFO produces worse ETAs everywhere distance actually carries
information.

### What already exists (and why this is tractable)

- **Geofence geometry** — the `venues` table (migration 134) already gives every
  airport/mall a `center_lat/lng + radius_m`. The "is this driver inside the
  airport" question is already answerable.
- **Per-area matching config** — `DispatchService.resolve_matching_config`
  already resolves `driver_matching_algorithm` with a service-area override over
  global app settings. A new algorithm value plugs in here.
- **Algorithm plug-point** — `select_driver_by_algorithm` already branches on
  algorithm name (`nearest`, `rating_based`, `round_robin`, `combined`).
- **Atomic claim + race guard** — `claim_driver` / `claim_driver_atomic`
  (`UPDATE ... WHERE is_available=true`) already prevents double-assignment and
  is reused unchanged.

The **only** missing primitive is *"when did this driver enter the zone"* — there
is no `entered_zone_at` anywhere today. Everything below flows from creating and
maintaining that one timestamp.

## Decision

Add an **opt-in, per-venue FIFO dispatch mode**, gated behind a
`venues.fifo_enabled` flag (default `false`). When a ride's pickup falls inside a
`fifo_enabled` venue, dispatch orders candidate drivers by **zone-entry time
ascending** (longest wait first) instead of by ETA/distance, restricted to
drivers currently queued for that venue.

Nothing changes anywhere until an admin enables the flag on a specific venue, so
the airport queue can be A/B'd against nearest-matching before any expansion.

### Geography: a terminal and its feeder lots are separate venues

An airport has two physically distinct places, typically 0.5–2 km apart, and they
are **separate `venues` rows**:

- **Terminal venue** (`fifo_enabled = true`) — where the rider's pickup pin lands.
  Its `center/radius_m` is the *pickup-detection* geofence (the role venues
  already play today). This is also the **shared queue key**.
- **Feeder-lot venue(s)** — where drivers physically **wait**. Each is its own
  venue row carrying a new `queue_for_venue_id` that points at the terminal. Its
  `center/radius_m` is the *queue-entry* geofence.

Multiple feeder lots may point at the same terminal (cell-phone lot + overflow
lot). All drivers in any lot of a terminal share **one** FIFO line: entering *any*
feeder lot stamps `drivers.zone_venue_id = <terminal id>` (not the lot id), so
ordering is a single `WHERE zone_venue_id = <terminal> ORDER BY zone_entered_at`
across every feeder lot at once. A driver waiting in Lot A and one in Lot B are
correctly interleaved by wait time, not segregated per lot.

Modelling lots as full venue rows (rather than inline `lot_*` columns on the
terminal) means an admin draws each lot with the **existing** venues admin UI —
the only new inputs are the `fifo_enabled` toggle (on the terminal) and a
`queue_for_venue_id` selector (on each lot).

Admin setup for an airport:
1. Create the terminal venue, toggle `fifo_enabled` on.
2. Create each feeder-lot venue, set `queue_for_venue_id` → the terminal.

### Queue mechanics

- **Entry** — on each driver location update, if the point is inside a **feeder-lot**
  venue radius, resolve that lot's `queue_for_venue_id` (the terminal) and, if the
  driver was not already queued for that terminal, stamp
  `drivers.zone_venue_id = <terminal id>` + `drivers.zone_entered_at = now`.
- **Ordering** — `ORDER BY zone_entered_at ASC` among drivers with
  `zone_venue_id = <terminal>` who pass the normal eligibility set (online,
  available, verified, vehicle-type, WAV if required, present heartbeat). Drivers
  across all feeder lots of that terminal are interleaved by wait time.
- **Exit resets to back** — leaving the feeder-lot radius (with hysteresis) clears
  `zone_venue_id`/`zone_entered_at`. Re-entering any feeder lot re-stamps `now`
  → back of line.

### Edge-case policies (the decisions that are easy to get wrong)

1. **Took a ride and left → back of line on return.** *Falls out of the geometry
   for free* — accepting a fare drives the driver out of the radius, which clears
   their entry stamp. No special "did this exit correspond to a fare" logic is
   needed. Leaving is leaving. This is the intended, fair behaviour: they got a
   ride; they re-queue like everyone else.

2. **Left without a fare (gas/coffee/washroom) → also back of line.** Same
   mechanism. We deliberately ship **no** grace window in v1 — a simple
   "exit = lose your spot" rule generates far fewer disputes than a timed
   grace exception. Revisit only if drivers ask for it.

3. **Decline / offer-timeout while still in the zone → 3-minute cooldown, KEEP
   position.** The driver is not physically leaving, so the geofence won't move
   them. We set a per-driver-per-venue cooldown key
   (`spinr:zone_skip:{venue_id}:{driver_id}`, TTL 180 s); FIFO ordering skips
   cooled-down drivers but preserves their `zone_entered_at`. This is the middle
   ground chosen over (a) lose-position-on-decline, which punishes one legitimate
   decline of a bad match, and (b) no-penalty, which lets a driver sit at #1
   cherry-picking short fares while everyone behind waits.

4. **WAV / vehicle-type cannot be purely positional.** FIFO is "first *eligible*
   in line." A WAV request skips past #1–#11 to the first queued WAV driver; a
   non-matching vehicle-type is skipped without losing its own position.

5. **Driver goes offline while queued → dropped; re-joins at back on return.**

### GPS hysteresis (the real operational risk)

A driver parked *at the edge* of the radius will have GPS flicker them in and out,
which would silently reset their position while they never moved — the #1 source
of "the app screwed me" airport tickets. Mitigation:

- **Enter** when inside `radius_m`.
- **Exit** only when **clearly** outside — beyond `radius_m + EXIT_BUFFER_M`
  (default 50 m) for at least `EXIT_DEBOUNCE_S` (default 30 s) of continuous
  out-of-zone fixes. A brief excursion or jittery fix does not count as leaving.

## Consequences

**Positive**
- Fair, predictable ordering at the one location where wait-time is the right
  signal. Directly supports driver retention and the 0%-commission,
  driver-respect brand — without any control-of-work pattern (no mandated shifts;
  a driver is free to leave the queue at any time).
- Low blast radius: additive algorithm, gated by a default-off flag, scoped to one
  venue. The ride state machine, atomic claim, offer-timeout loop, fare, surge,
  and insurance-period logic are all untouched.

**Negative / costs**
- Adds one geofence check to the driver-location hot path (must stay within the
  `<150 ms` location-write SLA — it is a single distance calc over the small set
  of cached active FIFO venues).
- New driver-app "#N in line" UI is required — without visible position, FIFO is a
  black box and loses the fairness benefit it exists to provide.
- Some new support load from queue-position disputes, mostly GPS-jitter driven,
  mitigated by the hysteresis buffer above.

**Neutral**
- Rider ETA is ~unchanged at the airport (drivers are equidistant), so match-rate
  and dispatch-latency KPIs should not move materially. Watch them during A/B.

## Rollback

- Set `venues.fifo_enabled = false` on the venue → dispatch instantly reverts to
  nearest/ETA matching for that venue. No deploy needed.
- The `zone_venue_id` / `zone_entered_at` columns, `fifo_enabled` flag, and
  `queue_for_venue_id` link are additive and nullable; dropping them is a clean
  reverse migration if the feature is abandoned.

## Implementation note

Decomposed into 8 commit-sized subtasks (this ADR is #1). Backend changes carry
dispatch-coverage (`≥80%`) and zone-queue unit tests; the migration goes through
`spinr-migration-reviewer` for index-with-query-pattern and rollback review.
