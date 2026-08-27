# Database Query Optimization — Audit & Recommendations

**Date:** 2026-08-26 · **Updated:** 2026-08-27 (PR #4579 review follow-ups — measured `EXPLAIN` results in §2.2, dispatch-actor timeline in §3.2, per-site split of P0 #7, resolution of the deferred `EXPLAIN` item)
**Scope:** Backend API call sites, background loops, and live Supabase query statistics
**Project:** `spinrmobileapp` (`soavhtdhefowwvforzwb`, ca-central-1, Postgres 17.6)
**Status:** **Recommendations only — no code, migration, or database change was made by this audit.**

---

## 1. Executive summary

### The question asked

> "Ride requests aren't taking time because there are few drivers — which APIs are calling whole-table data, is it really necessary, what are the big queries, what are the repeated queries, and what's the impact of optimizing them?"

### The answer

**The slowness is not caused by data volume, and it is not caused by the number of drivers.** The live database is tiny:

| Table | Live rows |
|---|---|
| `rides` | 202 |
| `drivers` | 212 |
| `users` | 1,138 |
| `driver_documents` | 1,902 |
| `ride_offers` | 15 |

Against those 202 rides, the backend issued **8.6 million PostgREST requests in 96 days** (2026-05-22 → 2026-08-26). That ratio is the finding. The cost is in **how many separate round-trips each operation makes**, not in how many rows come back.

Four root causes, ranked by impact on a rider pressing "Request ride":

1. **Sequential round-trips on the hot path.** `POST /rides` issues ~22 separate PostgREST calls before dispatch begins; each dispatch attempt issues ~25–30 more. Every one is an individual HTTPS request marshalled through a thread pool. A single ride searching for 5 minutes generates roughly **800–900 database round-trips** because three independent actors each re-run the full dispatch attempt on their own timer.
2. **Three calls that block the event loop.** `routes/drivers/earnings.py` (2 sites) and `routes/drivers/ride_reads.py` (1 site) call the synchronous Supabase client directly without `run_sync`. Each one freezes the entire process — *every* concurrent request, including ride bookings — for the duration of that query. This is the most likely explanation for "it's slow even when nothing is happening."
3. **One missing index.** `driver_documents` has no index on `driver_id`. It has absorbed **449,391 sequential scans reading 702.7 million tuples** from a 1,902-row table. The query `WHERE driver_id = ? AND status = ?` ran 388,357 times and is the **second-most expensive query in the database by total execution time** (259 seconds cumulative).
4. **Background polling.** There are **40 background loops** (the documentation says 18). Fourteen have no leader lock, so they run on every replica of every deployment — and Railway is still running a stale standby against the same database (`ACTION_ITEMS.md` C5). The five most-called queries in the entire database are all loop polls, at 850,000–875,000 calls each.

### Is fetching whole tables necessary?

**No — in every case examined there is a bounded, aggregated, or cached alternative**, and several "fetch everything" call sites are already silently broken:

`repositories/_base.py:868` — `get_rows()` has **no default limit**. When a caller omits `limit`, no `LIMIT` clause is emitted and PostgREST silently truncates the result at its default `db-max-rows` of **1,000**. Callers that believe they are reading a full set are already receiving a truncated one, with no error. This affects a regulatory export: `services/data_transfer/bundle_document_uploader.py:198` reads `driver_insurance_periods` unbounded — insurance-period rows are a 7-year SGI audit obligation, and a driver with more than 1,000 period transitions exports incomplete data with no warning.

There is also a latent bug at `repositories/_base.py:887`:

```python
if limit is not None and offset is not None:
    q = q.range(offset, offset + limit - 1)
elif limit:              # ← falsy check: limit=0 skips this branch entirely
    q = q.limit(limit)
```

`limit=0` produces an **unbounded query**. Roughly 40 call sites pass `limit=len(some_list)`; the ones that pass a bare `len(...)` on a possibly-empty list hit this. Most are paired with `{"$in": []}`, which independently matches nothing, so it is latent rather than actively firing — but the layer is one refactor away from a full-table read.

---

## 2. Live database evidence

All figures from `pg_stat_statements` and `pg_stat_user_tables`, statistics accumulated since **2026-05-22 15:13 UTC** (96 days).

### 2.1 The most-repeated queries

Every one of the top five is a background loop polling on a timer. None is user-initiated.

| Calls | Query (abbreviated) | Total time | Mean | Source |
|---:|---|---:|---:|---|
| 874,739 | `rides` WHERE `status = ?` ORDER BY `ride_started_at` | 44.7 s | 0.05 ms | stale-ride / in-progress sweepers |
| 861,309 | `push_retry_queue` pending rows | 43.6 s | 0.05 ms | `push_retry` loop, 30 s, **no leader lock** |
| 859,022 | `rides` WHERE `status = ?` (rider_id, started_at) | 37.6 s | 0.04 ms | safety check-in / gap monitor |
| 853,575 | `ride_routes` WHERE `processing_status` ORDER BY `processing_claimed_at` | 70.9 s | 0.08 ms | `route_finalizer`, 15 s, **no leader lock** |
| 853,571 | `ride_routes` WHERE `processing_status` ORDER BY `next_retry_at` | 34.8 s | 0.04 ms | `route_finalizer` (2nd query, same tick) |
| 392,212 | `settings` id probe | 35.7 s | 0.09 ms | config polling |
| **388,357** | **`driver_documents` WHERE `driver_id` AND `status`** | **259.0 s** | **0.67 ms** | **document expiry — missing index** |
| 241,811 | `ride_offers` WHERE `status='pending'` AND `expires_at < now` | 19.4 s | 0.08 ms | `offer_expiry_reaper`, 10 s |
| 151,805 | `ride_location_gap_events` WHERE `status='open'` | 10.0 s | 0.07 ms | `route_gap_monitor`, 15 s, **no leader lock** |
| ~391,673 | `UPDATE rides SET cancellation_… WHERE status='searching'` (6 statement shapes) | 26.3 s | — | `stuck_ride_sweeper`, 60 s, **no leader lock** |

**Reading the stuck-sweeper number:** 96 days at one execution per 60 s = ~138,000 expected executions. Observed ≈ 391,673, i.e. **~2.8× more processes than one** were running that loop. It is an atomic claim (`UPDATE … WHERE status='searching' AND ride_requested_at < cutoff`), so concurrent replicas do not double-cancel rides — but each replica still executes an unbounded `UPDATE` against `rides` every minute.

### 2.2 The most expensive queries (by cumulative execution time)

These are server-side execution times only — network, TLS, and PostgREST overhead sit on top.

| Total | Calls | Mean | Query |
|---:|---:|---:|---|
| 473.0 s | 13,123 | **36.04 ms** | `SELECT users.* WHERE id = ? AND deleted_at IS NULL` |
| 259.0 s | 388,357 | 0.67 ms | `SELECT driver_documents.* WHERE driver_id = ? AND status = ?` |
| 228.2 s | 10,214 | 22.34 ms | `rides` payment-status scan (payment retry loop) |
| 185.6 s | 20,630 | 9.00 ms | `driver_statements` WHERE `period_type`, `period_start` |
| 170.9 s | 11,740 | 14.56 ms | `SELECT users.* WHERE id = ?` |
| 139.0 s | 3,061 | **45.40 ms** | `SELECT users.* WHERE id = ANY(?)` |
| 107.7 s | 5,274 | 20.42 ms | `SELECT drivers.* WHERE user_id = ?` |
| 101.2 s | 7,352 | 13.76 ms | `SELECT drivers.* WHERE status = ANY(?)` |
| 68.8 s | 2,033 | 33.84 ms | `SELECT drivers.*` — **no filter at all** |
| 58.1 s | 508 | **114.46 ms** | `SELECT drivers.* WHERE id = ?` |

**This table is the single most important piece of evidence in the audit.** A primary-key lookup of one row from a 212-row table should be sub-millisecond. Measured means of 36 ms, 45 ms, and 114 ms are two to three orders of magnitude off. The common factor in every slow entry is **`SELECT *` on `users` or `drivers`** — wide rows containing encrypted PII columns, `documents` JSONB, and `profile_image` (which `routes/admin/drivers.py:513-519` documents can be a base64 data URI). PostgREST also wraps every result in `json_agg`, so wide rows are serialized to JSON server-side before transmission.

> **Follow-up performed (2026-08-27):** `EXPLAIN (ANALYZE, BUFFERS)` was run against production with live parameter values.
> - `users` by PK: **Index Scan, 0.76 ms**. `drivers` by `user_id`: **Index Scan, 0.13 ms**. `refresh_tokens` by `user_id`: **Index Scan, 0.09 ms**. `rides` by `status`: **Index Scan, 0.10 ms**. All four are correctly indexed and fast today — so the historical 36–114 ms means are **not plan problems**; they reflect row width (`SELECT *` on ~1.4–1.6 KB rows serialized through `json_agg`) and past load. Column projection (P1 #9) is the fix, not an index.
> - `driver_documents` WHERE `driver_id` AND `status`: **Seq Scan confirmed** — 1,892 of 1,902 rows discarded by the filter, 157 buffer reads, 0.58 ms/call. `index_advisor` estimates planner cost **186.7 → 11.0 (−94%)** with a `driver_id` index.
> - RLS nuance: the backend's PostgREST traffic runs as `service_role`, which **bypasses RLS** — so the 57 `auth_rls_initplan` warnings do not explain these backend query times. They still matter for `anon`/`authenticated` direct access (storage, any future client-side reads).

### 2.3 Sequential scans on hot tables

| Table | Rows | Seq scans | Tuples read via seq scan | Index scans |
|---|---:|---:|---:|---:|
| `driver_documents` | 1,902 | **449,391** | **702,682,770** | 648 |
| `refresh_tokens` | 902 | 44,511 | 33,803,981 | 17,896 |
| `users` | 1,138 | 70,953 | 17,289,552 | 257,288 |
| `ride_routes` | 10 | 672,868 | 12,789,109 | 1,013,864 |
| `drivers` | 212 | 155,360 | 10,855,665 | 225,626 |
| `rides` | 202 | 78,488 | 6,603,418 | 3,090,047 |
| `ride_offers` | 15 | 243,379 | 6,626,778 | 6,423 |

`driver_documents` reading 702 million tuples from a 1,902-row table is the clearest single defect in the database. 648 index scans against 449,391 sequential scans means essentially nothing is using an index on this table.

### 2.4 Storage

| Table | Size | Rows | Note |
|---|---:|---:|---|
| `surge_pricing` | **68 MB** | 35,006 | Largest table in the database — all of it stale history (§6.2) |
| `document_files` | 24 MB | 31 | |
| `driver_location_history` | 6.1 MB | 1,716 | |
| `audit_logs` | 5.1 MB | 7,125 | |
| `rides` | 2.9 MB | 202 | ~14 KB/row — GPS polyline JSONB |

### 2.5 Advisor findings (257 total)

| Count | Level | Type |
|---:|---|---|
| 154 | INFO | Unused index |
| 57 | WARN | `auth_rls_initplan` — RLS policy re-evaluates `auth.*()` per row |
| 23 | WARN | Multiple permissive policies for the same role/action |
| 14 | INFO | Unindexed foreign key |
| 8 | WARN | Duplicate index |
| 1 | INFO | `auth_db_connections_absolute` |

**Unindexed foreign keys on hot tables:** `driver_documents.driver_id`, `driver_documents.requirement_id`, `push_retry_queue.user_id`, `refresh_tokens.replaced_by`, `drivers.referred_by`.

**Exact-duplicate index pairs (one of each is pure write overhead):**
- `surge_pricing`: `idx_surge_pricing_area` ≡ `idx_surge_pricing_area_created`
- `driver_location_history`: `idx_dlh_driver` ≡ `idx_driver_location_history_driver_id_timestamp`
- `driver_location_history`: `idx_dlh_ride` ≡ `idx_driver_location_history_ride_id_timestamp`

**On the 154 "unused" indexes:** do not mass-drop these. Statistics cover 96 days of a pre-launch system; a seasonal or admin-only query path may legitimately not have run. Only the three exact duplicates above are safe to drop on this evidence, because their twin proves the access pattern is served.

---

## 3. The ride-request hot path

### 3.1 Round-trip inventory

**`POST /rides`** (`routes/rides/booking.py:376`) — ~22 sequential PostgREST calls before dispatch starts:

| Step | File:line | Table | Note |
|---|---|---|---|
| Idempotency check | `booking.py:413` | `rides` | |
| User lookup | `booking.py:423` | `users` | **`find_one` → `SELECT *`, uncached** — bypasses the 30 s-cached `get_user_by_id` |
| Active-ride guard | `booking.py:494` | `rides` | |
| Scheduled-ride guard | `booking.py:523` | `rides` | `limit=200`, result only used for `len()` |
| Unpaid-ride guard | `booking.py:581` | `rides` | |
| Service areas | `booking.py:614` | `service_areas` | `limit=500`, `SELECT *` incl. `polygon` JSONB |
| Area resolution ×N | `booking.py:623/655/682` | RPC | One per pickup, dropoff, and each stop |
| Vehicle types | `booking.py:732` | `vehicle_types` | |
| Fare config | `booking.py:734` | `fare_configs` | conditional |
| Area fees | `booking.py:873` | `area_fees` | |
| Wallet / corporate | `booking.py:898/976/1116` | `wallets`, `corporate_members` | path-dependent |
| Insert | `booking.py:1212` | `rides` | |
| Updates ×3 | `booking.py:1297/1342/1582/1608` | `rides` | promo, fare snapshot, route |
| Re-read | `booking.py:1466` | `rides` | full row re-fetched after insert |

**Each dispatch attempt** (`routes/rides/matching.py:159`) — ~25–30 more calls. The notable redundancies:

- **The ride's own `service_areas` row is read five times per attempt**: `matching.py:223` (via `dispatch_service.py:311`), `:408`, `spinr_pass.py:285`, `:546`, `:833`. Plus `utils/service_area_scope.py:85` reads the **entire `service_areas` table** on every attempt and every retry, uncached.
- **Driver claim loop** (`matching.py:728-729`): for each candidate, `claim_driver_atomic()` invalidates the Redis driver cache (`driver_repo.py:252` and `:278`), then the very next line calls `get_driver_by_id()` — a **guaranteed cache miss**, so a full `SELECT *` on `drivers` per claim. The loop correctly breaks at `max_offers` successful claims, but failed claims still walk and still pay both round-trips.
- **Per-driver quest lookup** (`matching.py:860`): `quest_progress` joined to `quests`, one sequential query per claimed driver, inside the notify loop.
- **Subscription quota check** (`matching.py:497` → `spinr_pass.py:498`): runs `driver_subscriptions` **and** a `rides`-completions read with `limit=10000` on **every** dispatch attempt whenever the ride has a `service_area_id` — including in areas where no driver holds a pass.

**What the geo filtering does right:** dispatch pushes a lat/lng bounding box into SQL (`dispatch_service.py:85 dispatch_geo_bounds()`) and only runs exact haversine in Python over the box-filtered set, with column projection (`matching.py:297`). It does **not** fetch all drivers globally. At 212 drivers this is entirely appropriate — no change recommended.

### 3.2 Why one ride generates ~800–900 round-trips

Three independent actors each re-run the full dispatch attempt for the same ride:

| Actor | Interval | File |
|---|---|---|
| Dispatch retry chain | 10 s, up to 30 attempts (~5 min) | `matching.py:77`, `:690` |
| Batch-offer timeout handler | 15 s per offer round | `matching.py:1272`, `:1322` |
| `offer_expiry_reaper` loop | 10 s | `utils/offer_expiry_reaper.py:122` |

Offer expiry itself costs ~6 queries per offered driver (`process_expired_offer` at `matching.py:1174`: update offer → update acceptance rate → set available → insurance-period RPC → re-read driver). Multiply by ~20 offer cycles in a 5-minute search and the arithmetic reaches 800–900 round-trips for one ride request.

Timeline for a single ride nobody accepts (each `attempt` ≈ 25–30 queries, each `expiry` ≈ 6 queries × offered drivers):

```
t=0s    POST /rides (~22 q) ──► dispatch attempt #1 ──► offers sent (15 s timers start)
t=10s   retry chain fires        ──► attempt #2
t=10s   offer_expiry_reaper tick ──► (offers not yet expired — scan only)
t=15s   batch-offer timeout      ──► expiry (~6 q × drivers) ──► attempt #3
t=20s   retry chain fires        ──► attempt #4
t=20s   offer_expiry_reaper tick ──► finds expired offers ──► expiry ──► attempt #5
  …     the three actors keep overlapping every 10–15 s, each re-running the full attempt
t=300s  ride_search_timeout ──► auto-cancel (stuck_ride_sweeper would also catch it on its 60 s cadence)
        ≈ 20 offer cycles × (~18 expiry + ~28 attempt queries) ≈ 800–900 round-trips
```

### 3.3 Three calls that block the entire process

These use the **synchronous** Supabase client directly, with no `run_sync` offload. In an asyncio service, one blocking call stalls every other in-flight request on that worker:

| File:line | Endpoint | Query |
|---|---|---|
| `routes/drivers/earnings.py:117-122` | `GET /drivers/balance` | `ride_incentive_claims` `.in_("ride_id", …)` over **up to 10,000 ride ids** |
| `routes/drivers/earnings.py:405-410` | `GET /drivers/earnings` | same pattern |
| `routes/drivers/ride_reads.py:357-362` | `GET /drivers/rides/history` | same pattern |

The correct pattern already exists three files away — `routes/rides/queries.py:138-146` wraps the identical construct in `run_sync`. This is a mechanical fix.

### 3.4 Connection pool sizing

`supabase_client.py:30-38` builds the HTTP client with:

```python
http2=False,
limits=httpx.Limits(keepalive_expiry=15),
```

Only `keepalive_expiry` is set, so `max_keepalive_connections` stays at httpx's default of **20** — while the database thread pool runs up to **64** workers (`repositories/_base.py:161`). Above 20 concurrent queries, every additional query pays a fresh TCP + TLS handshake, and idle connections are dropped after 15 seconds of quiet. HTTP/2 is disabled (there is a documented reason — an h2 hpack race), so each connection carries one request at a time.

---

## 4. Whole-table, unbounded, and N+1 call sites

Grouped by the priority order requested: ride latency → background load → admin.

### 4.1 Rider / driver hot path

| File:line | Endpoint | Issue | Necessary? |
|---|---|---|---|
| `routes/rides/estimates.py:406` | `POST /rides/estimate` | `calculate_airport_fee()` called **without** `_all_areas`, so it re-fetches `service_areas` — even though `_est_all_areas` is already in scope from `:185`. `booking.py:851` passes it correctly. | No — one-line fix |
| `routes/rides/estimates.py:513` → `features.py:685` | `POST /rides/estimate` | `area_fees` fetched **once per vehicle type**; the row set is identical every iteration. `calculate_all_fees` already accepts `_all_areas`/`_matched_area` but the `area_fees` fetch is unconditional. | No — hoist out of loop |
| `routes/rides/estimates.py:185` + fare-cache miss path (`routes/fares.py:166`) | `POST /rides/estimate` | `service_areas` read 3–4× per estimate, each `SELECT *` including `polygon` JSONB | No — fetch once / cache |
| `routes/drivers/earnings.py:57` and `:91` | `GET /drivers/balance` | The **same** 10,000-row `rides` query runs twice; the second result is used only for `len()` | No — `count_documents` |
| `routes/drivers/earnings.py:57/134/173/208` | `GET /drivers/balance` | Four bulk scans (10k/10k/5k/10k), all `SELECT *`, summed in Python | No — SQL aggregates |
| `routes/rides/queries.py:254` | `GET /rides/stats` | `limit=10000` `SELECT *`; with `period=all` the date filter is dropped entirely — the rider's whole history including GPS polylines, for a count and two sums | No — aggregate RPC |
| `routes/promotions.py:529-537` | `GET /promo/available` | `count_documents(...)` whose **result is never assigned** — a wasted round-trip on every call | **Dead code** |
| `routes/payments.py:623/666/690` | `POST /payments/confirm` | `get_ride(ride_id)` called 3× for the same ride — **all three inside the single `confirm_payment` handler** (def at `payments.py:584`), so the fix is a plain hoist of one `get_ride` to the top of the handler; no cross-layer request-stash needed | No — reuse |
| `routes/maps_proxy.py:289` | `GET /maps/pickup-points` | All active venues (`limit=2000`, `SELECT *` incl. `pickup_points` JSONB) on every pin drop, no geo bound, no cache | No — radius filter |
| ~70 driver endpoints | various | Re-fetch the driver row uncached with `SELECT *`, although `dependencies/__init__.py:424` already fetched it via the 30 s-cached `get_driver_by_user_id_cached` and discarded it | No — reuse the cached row |
| `routes/rides/queries.py:407` | `GET /rides/{id}` | `find_one("service_areas")` per call, purely to read two static cancellation-fee numbers | No — cache |

### 4.2 Truly unbounded (`get_rows` with no `limit` at all → silent 1,000-row truncation)

| File:line | Table | Consequence |
|---|---|---|
| `services/data_transfer/bundle_document_uploader.py:198` | `driver_insurance_periods` | **Regulatory export silently truncated.** SGI/TNC audit obligation is 7 years of period transitions |
| `services/data_transfer/bundle_document_uploader.py:180` | `driver_documents` | Same truncation risk |
| `services/corporate_member_offboarding_service.py:59` | `rides` | Iterated to cancel each ride — a truncated read leaves rides uncancelled |
| `services/corporate_suspension_service.py:60` | `rides` | Same |
| `services/corporate_wallet_winddown_service.py:100` | `corporate_wallet_transactions` | Fetches all, uses only the recent head |
| `repositories/corporate_repo.py:499` | `corporate_wallets` | `SELECT *` with **no filter at all** — entire table |
| `routes/admin/sgi_forms.py:142/343/513` | `drivers` | `SELECT *` incl. encrypted PII, decrypted per row, sorted in Python |
| `routes/admin/drivers.py:3937`, `routes/admin/support_tickets.py:740` | `service_areas` | Small table, but unbounded by construction |

### 4.3 Background loops

**40 loops** are spawned in `core/lifespan.py` (the documentation in `CLAUDE.md` says 18 — that count is stale). **26 hold a Redis leader lock; 14 do not.** Every leader lock **fails open** on a Redis error (e.g. `surge_engine.py:483`, `offer_expiry_reaper.py:146`), so a single Redis blip converts all 26 into per-replica loops simultaneously.

Highest-cost loops:

| Loop | Interval | Leader lock | Per-tick cost |
|---|---|---|---|
| `route_finalizer` | 15 s | **No** | 2 `ride_routes` polls → 1.7 M calls total |
| `route_gap_monitor` | 15 s | **No** | `rides` in-progress `limit=500`, then **2 `driver_location_history` queries per ride** (N+1) |
| `stuck_ride_sweeper` | 60 s | **No** | Unbounded `UPDATE rides … WHERE status='searching'` |
| `push_retry` | 30 s | **No** | `push_retry_queue` + `users` join → 861 k calls |
| `safety_checkin` | 30 s | **No** | `rides` in-progress scan (the Redis NX there is per-ride dedupe, not a leader lock) |
| `document_expiry` | 12 h | **No** | Full `drivers` table `SELECT *` paged, then **per-driver `driver_documents` loop** — the source of the 700 M-tuple seq-scan storm |
| `driver_onboarding_reminders` | 15 min | **No** | `service_areas` `limit=1000` **every tick**, regardless of window |
| `surge_engine` | 120 s | Yes | See §6.2 |
| `driver_claim_reaper` | 60 s | Yes | `drivers` `limit=200`, then per-driver `ride_offers` + `rides` (N+1) |
| `auto_payout` (weekly batch) | 1 h poll | Partial | Per driver: `rides` ×2 at `limit=10000`, `driver_bonuses` 10 k, `payouts` 5 k |
| `subscription_expiry` | 6 h | Yes | `driver_subscriptions` `limit=500`, then per-row `drivers` fetch + update (N+1) |

**Deployment multiplier:** per `CLAUDE.md`, Railway and Fly both deploy from `main` in parallel, and `ACTION_ITEMS.md` C5 records that Railway is blocked but still live. Both run all 40 loops against this same database. An unlocked loop's query count is therefore multiplied by *(Fly replicas + Railway replicas)*.

### 4.4 Admin dashboards

| File:line | Endpoint | Issue |
|---|---|---|
| `routes/admin/drivers.py:646/651/734/797` | `GET /admin/drivers/stats` | 5,000 `drivers` `SELECT *` (encrypted PII) + 5,000 `users` `SELECT *` (**base64 `profile_image`**) + **50,000 rides** summed in Python; the full enriched driver array is returned in the response |
| `routes/admin/monitoring.py:123-134`, `routes/websocket.py:1303` | monitoring page + WS snapshot | `.execute()` with **no limit and no filter** — the entire `drivers` table on every poll; the projection at `:147`/`:229` explicitly includes `profile_image` |
| `routes/admin/messaging.py:59` | audience preview / send | `rides` `limit=50000` just to collect distinct `rider_id` |
| `routes/admin/subscriptions.py:168/219` | `GET /admin/subscriptions/stats` | Whole `driver_subscriptions` (10 k) + `subscription_payments` (20 k), then date-filtered in Python |
| `routes/admin/support.py:160` | disputes stats | `limit=10000` rows → 4 counts and a sum |
| `routes/admin/faqs.py:205/210/215` | `POST /admin/faqs/notify` | `users` `limit=10000` **three times**, only `len()` used |
| `routes/admin/drivers.py:2395/2412` | rider referral leaderboard | Whole `users` table, then **nested N+1** `count_documents` per referee |
| `routes/admin/drivers.py:158-180` | `_batch_fetch_drivers_and_users` | No `columns=` projection — feeds 8 admin endpoints, all shipping full PII rows |
| ~20 endpoints | various | `?limit=` query params with **no upper bound** — a client can request `?limit=1000000` |

**The model to copy already exists in this repo.** `GET /admin/payouts/overview` was already migrated to the `admin_payouts_overview_aggregates` RPC (migrations 159/303); its own code comment at `routes/admin/rides.py:2896-2899` records that this replaced two `limit=200000` scans. Its residual `limit=200000` fetch at `:2928` is window-bounded (`created_at >= prev_start`), so it reads a small slice in practice. Migrations 162, 164, and 204 provide further aggregate-RPC examples.

---

## 5. What the industry does

### 5.1 Ride-status updates: push, not polling — **DEFERRED per your instruction**

Uber found that **~80% of all network requests made by the Uber app were polling calls**, which inflated cold-start time as polling competed for resources and delayed UI rendering. They replaced it with RAMEN (Realtime Asynchronous Messaging Network) — a server-push platform originally on Server-Sent Events, later migrated to **gRPC bidirectional streaming over QUIC/HTTP3**. The server decides when state changed and pushes; the client never asks.

**Where Spinr stands:** the push half is already built and mandatory — `CLAUDE.md` requires every ride state change to emit a WebSocket event keyed to both rider and driver. Polling is the *redundant* layer sitting on top of a working push channel.

**Current cost, quantified** (analysis only — no change made):

| Poll | Interval | Queries/call | Per rider/minute |
|---|---|---|---|
| `GET /rides/{id}` while `searching` (`rider-app/app/ride-status.tsx:158-160`) | 3 s | 5 | 100 |
| `GET /rides/estimate` on ride-options (`ride-options.tsx:318`) | 15 s | ~10 on cache miss | ~40 |
| `GET /drivers/nearby` (`ride-options.tsx:319`) | 10 s | 2 | 12 |

**Impact if changed:** roughly an **80–90% reduction** in queries per searching rider. Two components: (a) make the poll cheap — `GET /rides/{id}` currently issues 5 queries, of which the `service_areas` read serves only two static numbers and the driver/user reads duplicate rows already cached; (b) lengthen the interval to 10–15 s as a fallback once WS delivery is confirmed reliable.

**Data-collection impact:** none negative. No new data is collected. The payload *shrinks* — today each 3-second poll returns a full ride row including GPS fields. Analytics that count ride-status reads as engagement signals would see volume drop; that is a metric-definition change, not data loss.

**Risk:** the WebSocket becomes a single point of failure for status updates. A fallback poll must remain (slower), plus a reconnect-and-resync path. This touches the live-tested ride-status surface *and* the mobile app, which is why it is correctly deferred.

### 5.2 Surge: streaming aggregation, not per-region table scans

Uber's **H3** hexagonal hierarchical spatial index buckets every supply/demand event into a hex cell; an aggregator service computes a surge map (cell → multiplier) every few seconds from Kafka streams and serves it from a global cache. Lyft computes Prime Time on **Apache Flink/Beam**, which cut their pricing latency from ~5 minutes to under 1 minute. Neither queries a driver table per region.

**Verified state of Spinr's surge engine today:**

```
service_areas: 6 rows — Regina, Regina Airport, riyadh, riyadh airport, Saskatoon, Saskatoon Airport
All 6: surge_enabled = false, surge_source = 'auto', surge_multiplier = 1
```

The loop's query filters `{is_active: true, surge_enabled: true}` **in the database** (`surge_engine.py:279`), so it returns zero rows and the per-area work never runs. **Answering your question directly: today the surge engine costs one cheap empty query every 2 minutes and does not fetch the driver fleet.** The code comment at `surge_engine.py:258-262` documents this as deliberate.

**But the `surge_pricing` table proves what happens when it is switched on.** 32,616 rows exist for two areas, written between 2026-05-23 06:18 and 2026-05-28 21:40 — a 5.6-day window:

| | Saskatoon | Regina |
|---|---:|---:|
| Rows written | 16,308 | 16,308 |
| Expected at one insert / 120 s | ~4,061 | ~4,061 |
| **Observed ratio** | **4.0×** | **4.0×** |

**Four processes ran the "leader-locked" surge loop concurrently.** That is the deployment multiplier (Fly replicas + Railway standby, separate or failed-open Redis locks) measured directly in production data. Those 32,616 rows are now 68 MB — the largest table in the database — of history nobody reads.

**What re-enabling surge would cost, unchanged:** `_count_supply_in_area` (`surge_engine.py:182` → `:152`) fetches `drivers` WHERE `is_online AND is_available` with `limit=5000` and **no geographic or area filter** — the entire online fleet — then filters by point-in-polygon in Python, **once per service area**, every tick, on every replica. Demand is fetched similarly (`rides` `limit=5000`, then the status filter applied in Python at `:111`). Plus one unconditional `INSERT` per area per tick even when the multiplier has not changed.

**Spinr-scale adaptation (the primitives already exist):**
1. **Prefetch once per tick.** `get_surge_status` was already fixed to fetch the driver set once and pass it down (`surge_engine.py:408-415`), but `calculate_surge_for_area` (`:237`) still calls `_count_supply_in_area(area)` with no `prefetched_drivers` — the live loop never received the fix. This is the one-line version of Uber's "aggregate once, fan out to cells".
2. **Turn on the PostGIS count.** Migration 170 already shipped `drivers.location_geog`, a partial GiST index, and the `drivers_available_in_polygon()` function. `_count_supply_spatial` (`:117`) is written and fallback-safe, gated behind `SURGE_SPATIAL_COUNT`, default off, tracked as `ACTION_ITEMS.md` D1. This removes the 5,000-row cap and the Python polygon loop entirely.
3. **Insert history only on change** — the multiplier is already compared at `:305`.
4. **Add retention** for `surge_pricing`, as exists for other high-volume tables.

**Stale-history cleanup (NOT executed — provided for your decision):**

```sql
-- Reclaims ~68 MB. All rows predate 2026-05-29; surge has been disabled since.
-- Verify nothing reads historical surge before running.
DELETE FROM public.surge_pricing WHERE created_at < '2026-06-01';
VACUUM FULL public.surge_pricing;   -- takes an exclusive lock; run in a window
```

### 5.3 Blocking I/O: never on the event loop

The standard for async services is absolute: no synchronous I/O on the event loop; blocking clients are offloaded to a worker pool, and the connection pool is sized to match concurrency. This repo already implements the mechanism (`run_sync`, `repositories/_base.py:269`, with circuit breaker, retry policy, and deadline propagation) and uses it correctly nearly everywhere. The three sites in §3.3 bypass it. The pool-sizing half (§3.4) is the same principle applied to the HTTP layer.

### 5.4 Aggregation and listing — matches your stated direction

Your instruction — *"use offset mechanism for listing the drivers, else use aggregate function for getting any analytics"* — **is** the industry standard:

- **Aggregation belongs in the database.** `COUNT`/`SUM`/`GROUP BY` in SQL; pre-computed rollups for dashboards; at Uber/Lyft scale, a real-time OLAP store (Apache Pinot, Druid) fed by streams. Never "fetch N thousand rows into the application and sum in Python."
- **Listings paginate.** `LIMIT`/`OFFSET` with a bounded maximum, plus column projection so a list endpoint never ships blobs it does not render.

Concrete recipe for this codebase:

| Pattern today | Replace with | Precedent in this repo |
|---|---|---|
| `len(get_rows(...))` | `count_documents()` (real `count="exact"` head request) | `repositories/_base.py:896` |
| Python `sum()` over fetched rows | SQL aggregate RPC | migrations 159, 162, 164, 204, 302, 303 |
| Unbounded list endpoint | `limit`/`offset` with `Query(..., ge=1, le=N)` | `routes/rides/queries.py` pagination |
| `SELECT *` on a list | `columns="…"` projection | `routes/rides/matching.py:297` |

Note one trap specific to this codebase: `db.py:94`'s legacy `count_documents` shim is `len(get_rows(..., limit=1000))` — it returns a **wrong count above 1,000** and transfers 1,000 rows to produce a number. Use `repositories/_base.py:896` instead.

---

## 6. Prioritized recommendations

Effort: **S** ≤ half a day · **M** 1–3 days · **L** > 3 days. Nothing below has been implemented.

Test gates (per `CLAUDE.md`, restated inline because they are mandatory, not advisory): every money-adjacent item — the `limit=0` fix (#3), the estimate-path de-dup (#6), and the aggregate RPCs (P1 #11) — requires a regression test proving **identical output** before/after plus a `spinr-money-auditor` pass; the index migrations (#1, #2) require `spinr-migration-reviewer`; anything touching dispatch (P2 #13, #14) requires `spinr-dispatch-reviewer` and a `mock_supabase_client` dry run.

### P0 — Do first (small, isolated, high return)

| # | Change | Impact | Risk & blast radius | Effort | Rollback |
|---|---|---|---|---|---|
| 1 | **Index `driver_documents (driver_id, status)`** plus covering indexes for the unindexed FKs `push_retry_queue.user_id`, `refresh_tokens.replaced_by` | Removes the largest single source of database work: 449 k seq scans / 702 M tuples; the 259 s query becomes an index lookup. **Measured 2026-08-27**: Seq Scan discards 1,892/1,902 rows per call; `index_advisor` cost 186.7 → 11.0 (−94%) | Additive DDL. Blast radius: writes to these tables pay a marginal index-maintenance cost — negligible at 1,902 rows. No read-path behavior change | S | `DROP INDEX` |
| 2 | **Drop the 3 exact-duplicate indexes** (§2.5) | Removes redundant write overhead on `surge_pricing` and `driver_location_history` | Low — each has a proven-identical twin still serving the pattern. Verify with `pg_indexes` immediately before | S | Recreate from the captured definition |
| 3 | **Fix `repositories/_base.py:887`** — `elif limit:` → `elif limit is not None:`, and make `limit=0` return no rows | Closes the latent unbounded-query path | **Highest blast radius in this list**: `get_rows` is the universal read helper. ~40 call sites pass `limit=len(...)`. Requires a regression test asserting `limit=0` emits no unbounded query, and a sweep of every `limit=len(` site to confirm none *relies* on today's behavior | S | Revert (pure code) |
| 4 | **Wrap the 3 blocking calls in `run_sync`** (§3.3) | Stops whole-process stalls that affect every concurrent request, including ride bookings | Low — mechanical, pattern copied from `routes/rides/queries.py:138`. Blast radius: `GET /drivers/balance`, `GET /drivers/earnings`, `GET /drivers/rides/history`. Same data, same shape | S | Revert |
| 5 | **Delete the dead query** at `routes/promotions.py:529-537` | One fewer round-trip on every `GET /promo/available` | None — result is never assigned | S | Revert |
| 6 | **Estimate-path de-duplication**: pass `_all_areas=_est_all_areas` at `estimates.py:406`; hoist the `area_fees` fetch out of the per-vehicle-type loop | Removes 1 + (N−1) queries per fare estimate, on a path polled every 15 s per rider with a 300 ms P95 target | Low but **fare-adjacent** — requires a test asserting byte-identical fare output. `booking.py:851` already proves the pattern. Blast radius: every fare quote | S | Revert |
| 7a | **Bound the unbounded corporate-service reads** — `corporate_member_offboarding_service.py:59`, `corporate_suspension_service.py:60`, `corporate_wallet_winddown_service.py:100` | Truncation here leaves rides **uncancelled** during offboarding/suspension — a correctness fix, not just performance | Medium consequence: these callers act on every returned row, so verify iteration semantics (paginate, don't just cap) | S | Revert |
| 7b | **Bound the `admin/sgi_forms.py:142/343/513` driver reads** | Unbounded `SELECT *` with encrypted PII decrypted per row | Also a PII-handling surface — pair the limit with `columns=` projection; give it a `spinr-security-auditor` pass | S | Revert |
| 7c | **Bound the low-blast sites** — `corporate_repo.py:499` (entire `corporate_wallets` table), `support_tickets.py:740`, `admin/drivers.py:3937` | Hygiene; tables are small today | Low | S | Revert |
| 8 | **Paginate the compliance export** (`bundle_document_uploader.py:180/198`) past the 1,000-row cap | **Regulatory**: fixes silent truncation of a 7-year SGI insurance-period audit obligation | Low technically, high in consequence-of-not-doing. Follow the existing paged pattern in `utils/document_expiry.py` | S | Revert |

### P1 — Your stated direction: pagination for listings, aggregates for analytics

| # | Change | Impact | Risk & blast radius | Effort |
|---|---|---|---|---|
| 9 | **Column projection on admin batch fetches** — `_batch_fetch_drivers_and_users` (`admin/drivers.py:158`), `admin/monitoring.py:147/229`, `admin/drivers.py:481/646/651` | Stops shipping encrypted PII, base64 `profile_image`, and GPS JSONB to dashboards. Directly targets the 36–114 ms `SELECT *` means in §2.2 | Medium — **must audit which fields the dashboard actually renders** before trimming (avatars may be displayed). Blast radius: 8 admin endpoints via the shared helper. A missing column is a visible UI break | M |
| 10 | **Offset pagination on driver listings** — `GET /admin/drivers/stats`, `GET /admin/monitoring/drivers`, and the unbounded WS `get_drivers_snapshot` | Bounds a currently unbounded per-poll full-table read | Ship **additive**: add `limit`/`offset` params whose defaults preserve today's response, then update the dashboard, then lower the default. Blast radius: admin dashboard + monitoring WS consumers. Per `CLAUDE.md`, any `admin-dashboard` change needs a real `npm run build`, and there is **no active visual-regression coverage** (B38) | M |
| 11 | **Aggregate RPCs for analytics** — start with `admin/drivers/stats` (50 k-row Python sum), then disputes stats, subscriptions stats, messaging stats, `GET /rides/stats` | Turns multi-second dashboard loads into single indexed aggregate queries | Medium. Money-adjacent totals must stay `NUMERIC` server-side, mirroring migration 303's approach, and must reproduce existing values exactly — including legacy-ride exclusions (`EXCLUDE_LEGACY_RIDES`, migrations 302/341). Needs `spinr-money-auditor` review | M–L |
| 12 | **Cap client-controlled `?limit=`** — `Query(default, ge=1, le=1000)` on the ~20 endpoints in §4.4 | Removes a trivial resource-exhaustion vector | Low. Blast radius: any caller legitimately requesting > cap — audit dashboard call sites first | S |
| 13 | **Replace `len(get_rows(...))` with `count_documents`** — `admin/faqs.py:205/210/215`, `drivers/earnings.py:91`, `rides/booking.py:523`, and the rest of §4.1 | Removes bulk transfers that produce a single integer | Low. Note `db.py:94`'s shim is itself wrong above 1,000 | S–M |
| 14 | **Reuse the already-cached driver row** across ~70 driver endpoints (`get_driver_by_user_id_cached`, or stash it on `current_user` in the dependency) | Removes one uncached `SELECT *` per driver request | Medium — the cache is 30 s; any endpoint needing strictly-fresh driver state (money, status transitions) must keep its direct read. Enumerate before converting | M |

### P2 — Structural (dispatch and background load)

| # | Change | Impact | Risk & blast radius | Effort |
|---|---|---|---|---|
| 15 | **Dispatch de-duplication** — read the ride's `service_areas` row once per attempt instead of 5×; cache the `service_areas` table in-process (mirroring `settings_loader.py`'s 60 s cache); batch the per-driver `quest_progress` into one `$in`; guard the `spinr_pass` quota check on "a finite pass exists in this area" | Cuts ~25–30 queries per dispatch attempt to roughly half | **Touches the live-tested dispatch surface.** Requires `spinr-dispatch-reviewer` + `mock_supabase_client` dry-run per `CLAUDE.md`'s state-machine gate. Blast radius: every ride assignment | M |
| 16 | **Fix the claim-loop cache invalidation** (`matching.py:728-729`) so `get_driver_by_id` is not a guaranteed miss | Removes one full `SELECT *` per claim attempt | Medium — the re-read exists deliberately to revalidate eligibility on fresh data. Any fix must preserve that revalidation, not skip it | M |
| 17 | **Leader locks on the 14 unlocked loops**, and fix fail-open so a Redis outage does not convert all 26 locked loops to per-replica at once | Divides the top-5 query counts by replica count | **Correctness-sensitive**: each loop must stay replay-safe; a lock that fails *closed* incorrectly means work stops silently. Needs the `spinr-background-loop` contract per loop | M–L |
| 18 | **Stop the stale Railway standby from running loops** against production (`ACTION_ITEMS.md` C5), or gate loops behind a role flag | Removes an entire duplicate set of all 40 loops | Operational, not code — but C5 also means failover is currently pointed at a stale build. Worth resolving on its own merits | S–M |
| 19 | **`route_gap_monitor` N+1** — batch the 2-per-ride `driver_location_history` reads into one `$in` per tick | Up to 1,000 queries per 15 s per replica become 2 | Low-medium; loop is not on a rider-facing path | S–M |
| 20 | **`document_expiry`**: add projection to the full `drivers` scan and batch the per-driver `driver_documents` reads | With P0 #1, eliminates the seq-scan storm | Low — 12 h loop, no user-facing path | S |
| 21 | **Size the HTTP connection pool** — `max_keepalive_connections` ≥ the DB thread-pool size; revisit `keepalive_expiry` | Removes TCP+TLS handshakes above 20 concurrent queries | Medium — a transport-layer change affecting every query. Leave `http2=False` alone (documented h2 race). Roll out to one replica and watch latency percentiles | S–M |

### P3 — Deferred by you

| # | Change | Note |
|---|---|---|
| 22 | **Rider polling → WebSocket-primary** (§5.1) | You asked to handle this later. Impact, data-collection effect, and risk are quantified in §5.1 for when you pick it up |
| 23 | **Surge re-enable preparation** (§5.2) | Not urgent while `surge_enabled = false` everywhere — but items 1–3 of §5.2 should land **before** surge is switched on, not after |
| 24 | **`EXPLAIN` investigation — partially resolved 2026-08-27** (see the §2.2 follow-up note): current plans are Index Scans at 0.09–0.76 ms, so the historical 36–114 ms means came from row width and load, not plans | Residual: review the 57 `auth_rls_initplan` warnings for `anon`/`authenticated` surfaces (backend `service_role` traffic bypasses RLS), and re-measure these means after P0 #4 (blocking calls) and P1 #9 (projection) land |

---

## 7. Scope and limits of this audit

**What was done:** three parallel static audits of `backend/` (DB layer, routes/services, dispatch + background loops); live inspection of the production Supabase project via `pg_stat_statements`, `pg_stat_user_tables`, `pg_indexes`, advisors, and direct queries against `service_areas` and `surge_pricing`; verification of every file:line reference cited above by reading the code.

**What was NOT done — no change of any kind was made:**
- No code was edited. No migration file was written. No index was created or dropped.
- No `DELETE`, `UPDATE`, or DDL was executed against the database. The only SQL run was read-only inspection. The `surge_pricing` cleanup statement in §5.2 is provided for your decision and was **not** executed.
- No tests were run; no load testing or benchmarking was performed. Every impact estimate is reasoned from code paths and observed query statistics, not measured against a before/after build.
- ~~`EXPLAIN` was not run against production~~ **Updated 2026-08-27:** `EXPLAIN (ANALYZE, BUFFERS)` and `index_advisor` were subsequently run against production (read-only) — results in the §2.2 follow-up note. Load testing and before/after benchmarks remain not done.
- Statistics cover 96 days from 2026-05-22. They include the period when surge was briefly enabled and may include development/testing traffic. Per-replica attribution is inferred from insert-rate arithmetic (§5.2), not from deployment telemetry.
- Frontend surfaces (`rider-app`, `driver-app`, `admin-dashboard`) were read only where they document polling intervals. No frontend build was run.

**Before implementing any P0/P1 item**, follow the `CLAUDE.md` pre-merge gates: blast-radius grep, additive-over-destructive preference, a Change Impact & Risk log entry, and — for anything touching dispatch, money, or corporate — the relevant `spinr-*` reviewer agent. Note that automated PR review is currently silent on this repo (`ACTION_ITEMS.md` C9/C7), so those reviews must be invoked manually.

---

## Sources

- [Uber's Real-Time Push Platform](https://www.uber.com/us/en/blog/real-time-push-platform/) — RAMEN; the ~80%-of-requests-are-polling finding
- [Uber's Next Gen Push Platform on gRPC](https://www.uber.com/blog/ubers-next-gen-push-platform-on-grpc/) — SSE → gRPC bidirectional streaming
- [H3: Uber's Hexagonal Hierarchical Spatial Index](https://www.uber.com/us/en/blog/h3/) — per-cell supply/demand aggregation for surge
- [Streaming your Lyft Ride Prices — Flink Forward SF 2019](https://www.slideshare.net/ThomasWeise/streaming-your-lyft-ride-prices-flink-forward-sf-2019) — Prime Time on Flink; 5 min → < 1 min
- [Real-time ML with Beam at Lyft](https://beam.apache.org/case-studies/lyft/index.html) — streaming pricing pipeline
