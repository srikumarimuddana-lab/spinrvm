# ADR 016 — One measured distance per ride: classified trail, finalizer authority, flag-switched readers

| | |
|---|---|
| Status | **Proposed** — decision taken 2026-09-11 by engineering with an architecture review and a systems/data-flow review (both recorded below); awaiting the three product sign-offs in §7 |
| Date | 2026-09-11 |
| Deciders | Engineering (Claude Code session `01Ro32rhyxmV2ws4MaPhPUSX`, on behalf of the founder); architecture review; systems-engineering review |
| Supersedes / relates | migrations 235 (v2 trail columns), 239/345 (unique indexes), 346 (`driver_period_distances` revisions), 348 (`compute_driver_phase_distances` v2); `utils/distance_reconciliation.py`; ADR 007 (deployment) is unaffected |

## 1. Context

Live-testing ride `SPR-T9NYPB` (2026-09-11, 13:51–14:10 UTC, 18 min) showed **four different distances** to four audiences:

| Figure | Where shown | How it is computed |
|---|---|---|
| **6.89 km** | booking quote, in-app receipt | planned route (`rides.planned_distance_km`); **the fare is locked on this** (`fare_breakdown_snapshot.locked_at`, `fare_lock_enabled = true` in production) |
| **8.98 km** | admin ride page "Actual trip"; SGI Period-3 audit row | inline settlement haversine over the trail (`utils/trip_distance.py` → `rides.route_quality.actual_distance_km_haversine`, `phase_distances.trip_in_progress`) |
| **12.24 km** | admin ride page "ACTUAL (GPS)"; `rides.distance_km`; **email + PDF receipt labels** | inline settlement's OSRM map-match, accepted by a 1/3×–3× band with **no gap gate** — it bridged GPS holes (the app crashed 7× mid-ride) with +3.3 km of invented road |
| **10.99 km** | driver daily "Distance Travelled" report | Postgres `compute_driver_phase_distances` over **all** `driver_location_history` rows for the day, `ORDER BY "timestamp"`, no dedup, no exclusion |

Why they disagree — verified in code and against production rows:

- **Three writers, two conventions, one table.** The v2 durable outbox writes `captured_at`, `timestamp = captured_at`, `source`, `(recording_session_id, sequence_number)` — acknowledged, deduped, ordered (125 rows). The WebSocket path (`routes/websocket.py:854-922` → `utils/breadcrumbs.py::persist_ride_breadcrumbs`) writes `timestamp = receive time` when the message carries no capture time, and leaves `captured_at`/`source`/sequence NULL. Three client senders feed it (`driver-app/hooks/useDriverDashboard.ts`): the REST-failure fallback (~797, correct), the once-a-minute idle breadcrumb (~881, no capture time, fires after a relaunch before the ride hydrates), and the **reconnect echo** (~1317: on every WS `auth_success` it re-sends the cached last fix with no `durable` flag — the backend defaults to durable — and no capture time). This ride: **13 rows that are exact copies of earlier fixes written 25–203 s late**; plotted, the path runs backwards at each one; they add ~2 km to the daily report.
- **Three readers, three sort keys, two dedup rules.** Inline settlement orders by `timestamp`; the finalizer (`utils/route_finalizer.py`) orders by `captured_at` and dedups on `(session, seq)` — so legacy rows are never deduped; the daily RPC orders by `"timestamp"` with no dedup.
- **Two OSRM acceptance rules.** Inline: 1/3×–3× of haversine. Finalizer (`resolve_measured_distance_km`): coverage ≥ 0.6, straight-connector share ≤ 25 %, ≥ 0.8× crow-flies, revisioned and audited (`ride_distance_recomputes`, `driver_period_distances` revisions). The finalizer is the better computer; the inline one wrote the number everyone saw.
- **`rides.distance_km` is overloaded** (`ride_complete.py:543-558`): the billed distance when fare-lock is off, a display copy of the measured value when it is on. `utils/email_receipt.py:135,178` and `utils/receipt_pdf.py:86,104` print it beside the *locked* price; `routes/rides/receipts.py:110-117` correctly substitutes the quoted distance.
- **Durations read `0m`** because production runs the v1 function (7 columns, no seconds) although `schema_migrations` records `348_phase_distances_fn_v2.sql` as applied on 2026-08-21. Recorded state and live state disagree; the runner will not re-apply it.
- **The raw trail is purged at 90 days** (retention Step C, migration 296). The long-lived record is `rides.*`, `ride_routes.observed_segments` (3 y) and `driver_period_distances` (revisioned). Any reader that re-sums raw points is guaranteed to drift from the materialised number and to read zero after 90 days.

Money is **not** currently affected: fare-lock is on, the rider paid the quote, the driver received 100 % of it, and `distance_reconciliation` is detection-only. The exposure is reporting, driver statistics, SGI audit rows, dispute evidence — and a dormant money path if `fare_lock_enabled` is ever switched off (`ride_complete.py:549` re-prices from the measured distance).

## 2. Decision

**One measured distance per ride, computed by the finalizer from a classified trail, stored with its provenance, and read by every surface through feature-flagged switches. Nothing is deleted, nothing is repurposed, every phase rolls back by a `settings` row.**

Concretely:

1. **Ingestion contract (the trail invariants).** A row is *trail-grade* only if `captured_at IS NOT NULL`, `(recording_session_id, sequence_number)` is present and unique (already `uq_dlh_session_sequence`), and `source` is from a closed set. A durable WebSocket point that carries **no capture time while the driver has an active ride is rejected at ingest** and counted (`spinr_drivers_trail_point_rejected_total{reason=no_capture_time}`) — it has no evidential value (it is the cached fix, or a pre-hydration heartbeat). Live pings (`durable:false`) never touch the table. Enforced by a `CHECK … NOT VALID` on the existing table (metadata-only, append-only compatible) plus the application rule.
2. **Classification, not deletion.** Additive columns `driver_location_history.trail_grade` (`durable | excluded`, nullable, derived at read when NULL) and `excluded_reason`. The 13 echo rows of `SPR-T9NYPB` are marked `excluded / ws_reconnect_echo` by id and stay in the table.
3. **One read function.** SQL `ride_trail_points(ride_id)` / `driver_trail_points(driver_id, from, to)`: graded, ordered by `captured_at` then `(session, seq)`, completion fix last, exact duplicates collapsed (`DISTINCT ON (driver_id, captured_at)` preferring the foreground outbox), legacy rows used only for a ride with **zero** durable rows. Settlement, the finalizer and the daily function all read through it. `timestamp` is never a sort key for v2 rows.
4. **The finalizer is the authority.** Inline settlement writes a *provisional* haversine; the finalizer writes the final value with `rides.measured_distance_basis ∈ {observed, reconstructed, planned_estimated}` and its revision. The inline OSRM promotion is demoted to record-only. `rides.distance_km` stops being overwritten under fare-lock (flagged); `actual_distance_km` is the measured column.
5. **Road-snap acceptance lives in the finalizer, once.** Match only observed segments already split at > 60 s / > 300 m / phase change; read OSRM `confidence` and reject < 0.5; accept a matching only if `matched / haversine ∈ [0.9, 1.6]` per segment; never sum a straight connector; a routed connector counts only across gaps ≤ 300 s and ≤ 2 km. Publish `observed_km` and `inferred_km` separately — dispute evidence needs the decomposition, not a floor.
6. **The daily report reads settled rides.** `trip_km` / `navigating_km` = Σ `driver_period_distances_current` (P3 / P2) for rides completed that Regina day; raw-point summing survives only for idle km, per-phase seconds, and a `provisional = true` figure for rides still in progress on "today".
7. **Receipts label the quoted distance** under fare-lock on every surface (one helper for email, PDF, in-app, admin modal).
8. **Client senders are fixed** (`[build]`): reconnect echo → `durable:false` + `captured_at`; idle heartbeat → `captured_at`, never durable while the ride is unhydrated; REST-failure fallback sends v2 identity so WS and REST are idempotent on the same point; the background task opens its own `recording_session_id` (sequence 58 landed between 50 and 51 because two producers shared one session).
9. **Fare-lock is the only supported billing basis** (product decision requested in §7). The dormant re-pricing path is left untouched until product confirms, then removed in a later phase.

## 3. Options considered

| | A · Inline settlement as authority; WS never writes during a v2 session | B · Split stores (durable table + Redis/TTL live positions) | **C · Classified trail, finalizer authority, flagged readers (chosen)** |
|---|---|---|---|
| Fixes the four-numbers problem | Partly — the inline OSRM band *is* the source of 12.24 | No — readers still disagree | Yes — one column, one basis, provenance visible |
| Echo contamination | Needs the server to know "a v2 session is active" (no such signal) | Yes, by construction | Yes — rejected at ingest, classified at read |
| REST-failure fallback trail | Lost | Lost | Kept (v2 identity over WS, deduped) |
| Idle km for clients on `idle_location_v2_enabled=false` (prod default) | Kept | Lost | Kept |
| Survives the 90-day purge | Yes | Unchanged | Yes (materialised + period view) |
| New infrastructure on Micro | None | Redis hash per driver or a TTL table | Two nullable columns, one `NOT VALID` check, no new index |
| Destructive to the live table | No | Yes (changes its meaning) | No (additive) |
| Rollback | Flag | Hard once live positions move | Flag per phase; columns inert when off |
| Effort | Medium | Medium–high | Medium — mostly wiring what exists |

Both reviews independently arrived at C. The architect argued A names the wrong authority and B solves only contamination; the systems engineer argued the table, both unique indexes and `idx_dlh_ride_captured` already exist, that a second unique index would break `insert_many_ignore_conflicts`'s `ON CONFLICT` inference, and that the live position already has a home in `drivers.lat/lng` behind the 3 s write gate.

## 4. Consequences

- Every distance a human sees traces to `rides.actual_distance_km` + `measured_distance_basis` + a revision. Driver statistics and SGI Period-3 rows move from inline haversine to the finalizer's reconstructed value — **append-only revisions, so auditable, but a change of basis the insurer conversation must accept (§7)**.
- `driver_location_history` keeps one schema; readers gain one function; nothing is repurposed.
- The daily RPC becomes a diagnostic plus the provisional "today" figure. Its v3 supersedes the never-effective v2.
- Raw rows at 30 concurrent rides, 6 h/day, 2 s cadence: ~400 k/day → ~36 M rows over 90 days ≈ 5 GB heap + ~6 GB indexes — **over Micro's 8 GB disk**. Decision required (§7): 30-day raw retention (the 3-year geometry already lives in `ride_routes`) and/or the Small tier once > 10 concurrent rides are routine. Capture cadence stays 2 s during live testing; accuracy matters more than disk right now.
- Two things stay explicitly out of scope: changing what riders pay (fare-lock already does that), and the crash fixes that caused this ride's GPS holes (branch `claude/ride-t9nypb-hardening`).

## 5. Delivery plan — every phase ships dark and flips by a `settings` row

| Phase | Ships | Rollback | Verify |
|---|---|---|---|
| **0 · Stop the bleeding** (no migration) | Ingest rule: reject durable WS points with no capture time during an active ride, + metric. Client: the three sender fixes + background-task session id (`[build]`). Inline OSRM promotion demoted behind `inline_road_snap_promotion_mode = legacy|off` (default `legacy` until shadow numbers are seen). Receipt distance-label helper for email/PDF/in-app/admin modal. | Flag to `legacy`; client fixes are safe to leave | Replay this ride's 138 points (synthetic coordinates, same structure) through `persist_ride_breadcrumbs`: 13 rejected, 125 accepted. Email receipt for `SPR-T9NYPB` shows 6.9 km. |
| **1 · Classify the trail** (migration 414) | `trail_grade`, `excluded_reason`, `CHECK … NOT VALID`; admin `UPDATE` marking the 13 rows by id; `spinr_drivers_trail_excluded_points_total{reason}` | Readers ignore the columns; nothing to revert | Row count unchanged; the 13 rows carry the reason |
| **2 · One read path** (415 functions, 416 RPC v3, 417 flags) | `ride_trail_points`, `driver_trail_points` (`REVOKE … FROM anon, authenticated`, migration-354 pattern); `compute_driver_phase_distances` v3 = v2 body + graded input + `COALESCE(captured_at, "timestamp")` order + `excluded_points`; settlement/finalizer/RPC behind `trail_reader_mode = legacy|shadow|canonical` | Flag to `legacy`; `DROP FUNCTION` | Shadow: per-ride disagreement metric vs today's fields, logged to `ride_distance_integrity_events`. This ride: v3 daily == settled P3 (8.98), not 10.99. |
| **3 · Canonical measured distance** (418) | `rides.measured_distance_basis`, `measured_distance_revision`; finalizer acceptance rule (§2.5); `distance_km` overwrite behind `receipt_distance_lock_quoted`; admin tiles relabelled *Billed (quoted) / Measured (basis) / Planned* | Flags | The 142 s hole yields `reconstructed` or `planned_estimated`, never `observed`. |
| **4 · Daily report from settled rides** | `driver_daily_stats.trip_km_settled`, `navigating_km_settled`, `distance_source`; rollup computes both; `daily_rollup_distance_source = raw_gps|shadow|settled_rides`; provisional "today" | Flag to `raw_gps` | Per driver-day: `trip_km` vs Σ P3 within max(0.5 km, 5 %) |
| **5 · Keep it true** | `distance_reconciliation` (daily 04:00) gains: road-snap rejections, excluded-point volume per driver (the tripwire for a broken outbox on an old build), report-vs-Σ-rides mismatch → integrity event + ERROR log (`domain=rides`, ids only); sequence-gap counter at ingest; client odometer in `ride_routes.completion_point` as a cross-check, never an authority | Loop change is additive | Zero `daily_report_mismatch` events over a test week |

Migration numbering: next free is 414; 348 must **not** be renumbered or re-run (recorded as applied); the real duplicate pairs 349/354 are already registered in `.known_duplicate_prefixes.json`. The 348 recorded-vs-live discrepancy is logged as its own finding for the migration-runner owner.

## 6. Risks (for the second-opinion list)

1. **Fare-lock off is a money path on the crude number.** `ride_complete.py:549` re-prices from `actual_distance_km` when the flag is off. Phase 0's demotion changes that mode's behaviour. Product must decide (§7) before Phase 0 ships.
2. **Insurer basis.** Period-3 audit rows move from haversine to the finalizer's reconstructed value. Auditable, but which basis is contractually "distance driven" is not an engineering call.
3. **Asynchronous authority.** The finalizer can end `incomplete` or exhaust retries; the daily Σ then mixes provisional and final bases. Acceptable if `distance_source` is displayed; decide whether an `incomplete` ride stays provisional forever or falls to `planned_estimated`.
4. **Reject-at-ingest vs accept-but-exclude.** Rejecting no-capture-time WS points means a driver on an old build with a broken outbox produces no trail. The REST-failure fallback still carries capture time, and the per-driver excluded/rejected metric is the tripwire. Confirm that is enough versus an app-version gate.
5. **Backfill on a hot table at Micro.** None is planned (grade derives at read); the only writes are the 13 explicit rows. If a wider backfill is ever wanted, it needs the batched pattern of migrations 345/346 and a dry run against row counts.

## 7. Decisions required from product (blocking the phases marked)

1. **Fare-lock permanent?** Riders pay the quote; measured distance is stats/evidence only. If yes, the re-pricing path is removed in Phase 3; if no, the finalizer's acceptance rule must gate billing too. *(Blocks Phase 0's demotion.)*
2. **Raw trail retention: 30 days or 90?** 90 days at 30 concurrent rides exceeds Micro's disk; 30 days is enough for disputes given `ride_routes` keeps the geometry 3 years. *(Blocks nothing today; blocks scale.)*
3. **Which basis is "distance driven" for SGI Period-3 rows** — the finalizer's reconstructed value (recommended) or the haversine lower bound? *(Blocks Phase 3.)*
4. **Display:** one headline number with its basis label on driver/admin surfaces, decomposition (`observed` + `inferred`) only in the dispute view — recommended. *(Blocks Phase 3 UI.)*

## 8. Evidence

- Ride `ec8d772a-ddad-4e58-bfe7-938c1f47d486`: `rides.route_quality` (`haversine 8.98`, `road_snapped 12.238`, `max_segment_gap_seconds 142`, `road_snap_accepted true`), `fare_breakdown_snapshot` (locked 13:51:27, "Ride fare (6.9 km)"), `driver_location_history` 138 rows (125 v2 + 13 legacy; twin analysis: 9 of 13 byte-identical to an earlier fix, 25–203 s late).
- Production `settings`: `fare_lock_enabled = true`, `fare_distance_basis = road`, `idle_location_v2_enabled = false`.
- Production function `compute_driver_phase_distances` returns 7 columns (v1) while `schema_migrations` lists `348_phase_distances_fn_v2.sql` applied 2026-08-21 23:29:45 UTC.
- Reviews: architecture (recommended C; five risks — §6 items 1–5) and systems/data-flow (ingestion invariants I1–I6, canonical read functions, capacity figures, migration order 414–417) — both 2026-09-11, session above.
- Post-mortem of the ride and the related crash fixes: `docs/change-log/2026-09-11-ride-t9nypb-hardening.md`.
