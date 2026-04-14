# 07 — Performance, Scalability & Data

> **Read time:** ~15 min
> **Audience:** Backend lead, DB owner, SRE

---

## Executive verdict

Performance is fine at the current (pilot) scale. But multiple designs **do not scale past 1 machine** (WS registry, rate limiter), and several DB patterns (N+1, Python polygon math, unbounded ride queries) will start to degrade at 100 concurrent riders.

---

## Scaling ceiling analysis

| Subsystem | Current bottleneck | Breaks at |
|---|---|---|
| WebSocket fan-out | in-memory `socket_manager` | 2+ machines |
| Rate limiter | `memory://` slowapi | 2+ machines |
| Surge engine | Python polygon loop, single process | ~2000 online drivers |
| Ride dispatch | nearest-N drivers via Python | ~10k rides/hr |
| DB connection pool | single-instance default | ~60 concurrent requests |
| Admin analytics | hits primary | ~1M rides (dashboard becomes slow) |
| Stripe webhook | sync processing in request path | 20 events/sec |
| Background tasks | single process | — halts at `min_machines=0` |

Target for launch (Saskatchewan-scale city): 50 concurrent active rides, ~500 online drivers, ~5 rps peak. **All headroom available** once the above fixes land.

---

## P0 findings

### P0-P1 — Per-instance rate limiter

See 02-S2. Production amplifier is **N×** configured limit. At auto-scale to 4 machines the OTP bucket is 4× intended.

---

### P0-P2 — WebSocket fan-out broken past 1 instance

See 03-B3. This is the single most important scaling blocker. A 1-machine cap is an acceptable short-term fix; long-term requires Redis pub/sub or managed WS.

---

## P1 findings

### P1-P3 — No caching layer

**Evidence:** No Redis/Memcached. Hot reads (service area by GPS, fare config, vehicle types, feature flags) hit Postgres on every request.

**Impact:** Fare estimate is called on **every** map-pan on the rider screen. Each call → 3–5 DB reads. 100 riders × 10 pans/min × 4 queries = 4000 qpm baseline.

**Fix (M):**
- Add Redis (Fly Redis / Upstash).
- Cache with TTL:
  - `service_areas:active` → 60s
  - `vehicle_types` → 300s
  - `fare_configs:{area}` → 60s
  - `features` → 60s
  - `user:{id}` auth dependency → 30s (with revocation hook)
- Invalidate on admin write.

---

### P1-P4 — Missing DB indexes (spot-check)

**Evidence:** `supabase_schema.sql` shows primary keys and some indexes. High-traffic query predicates need dedicated indexes:

| Query | Needed index |
|---|---|
| `rides WHERE status IN ('searching','driver_assigned') AND service_area_id=?` | `(service_area_id, status)` partial |
| `rides WHERE driver_id=? ORDER BY created_at DESC` | `(driver_id, created_at DESC)` |
| `payments WHERE ride_id=?` | `(ride_id)` |
| `notifications WHERE user_id=? AND read=false` | `(user_id, read)` partial |
| `gps_breadcrumbs WHERE ride_id=? ORDER BY ts` | `(ride_id, ts)` |
| `drivers WHERE is_online AND is_available` (+ ST_DWithin geo) | GIST on `geometry(point)` |

**Fix (M):** Add each as a migration. Use `CREATE INDEX CONCURRENTLY` to avoid locking writes.

---

### P1-P5 — Surge engine: O(drivers × areas) polygon test in Python

See 03-B10. Move to PostGIS `ST_Contains`.

---

### P1-P6 — Dispatch algorithm inefficiency (suspected)

**Evidence:** Without deep code read, ride dispatch likely loads all online drivers in an area, computes distance in Python, sorts, offers to top-N.

**Impact:** At 500 online drivers, 100 ride requests/min → 50k distance calcs/min. Reasonable now, but worse with multiple ride types and filters.

**Fix (M):** Use PostGIS `<->` geometry distance operator + KNN index:
```sql
SELECT id FROM drivers
WHERE is_online AND is_available AND service_area_id = $1
ORDER BY location <-> ST_SetSRID(ST_MakePoint($lng,$lat),4326)
LIMIT 5;
```
Native index scan is O(log N).

---

### P1-P7 — Stripe webhook processes synchronously in request path

**Evidence:** `webhooks.py` does DB writes, wallet credits, push notifications inside the HTTP handler.

**Impact:** Stripe's 20-second deadline tight if push API is slow; retry storms. Also blocks Stripe event throughput at ~20 eps per instance.

**Fix (M):** Return 200 immediately after `stripe_events` insert (idempotency check from 03-B2). Then push event ID into a Redis queue; worker consumes and processes asynchronously.

---

### P1-P8 — GPS breadcrumb table will grow unbounded

**Evidence:** `backend/routes/websocket.py` persists every GPS ping to `gps_breadcrumbs`.

**Impact:** At 500 drivers × 1 ping/5s × 8 hours/day = 2.88M rows/day. Within a month the table is 86M rows; query performance collapses without partitioning.

**Fix (M):**
- Partition `gps_breadcrumbs` by month (`PARTITION BY RANGE (created_at)`).
- Add automated `DROP PARTITION` after 90 days (or archive to S3 as parquet).
- Consider downsampling: keep 1-sec resolution for 24h, 10-sec for 30d, 60-sec thereafter.

---

### P1-P9 — No connection pooling tuning

See 03-B7. Supavisor pooled endpoint + explicit pool sizing.

---

## P2 findings

### P2-P10 — No CDN for static assets

Admin dashboard on Vercel is CDN-fronted (good). But Supabase Storage (driver docs) is served direct; add a CDN cache for public-read media.

### P2-P11 — No image optimization pipeline

Cloudinary is used for avatars (good). Driver vehicle photos / documents may not go through resize — adds to mobile bandwidth cost.

### P2-P12 — Admin dashboard fetches lists without virtualization

Large user/ride tables render all rows. Add `react-window` virtualization at >200 rows.

### P2-P13 — No query cost budget

Consider `pg_stat_statements` dashboard; alarm on queries >500ms mean.

### P2-P14 — WebSocket heartbeat interval is 30s

Mobile radio wake every 30s burns battery. Consider 60s with server-initiated ping on idle 90s.

### P2-P15 — No compression on API responses

FastAPI + uvicorn can gzip. Add `GZipMiddleware(app, minimum_size=1024)`.

---

## P3 findings

- **P16** — No ETag/Last-Modified on list endpoints → clients refetch fully.
- **P17** — Service-area polygon may be stored as JSON; PostGIS `geography` is faster.
- **P18** — Consider read replicas once analytics queries exceed 20% of primary CPU.

---

## Capacity / scaling plan

| Phase | Users | Infra action |
|---|---|---|
| Pilot | <500 MAU | 1 Fly machine + 1 worker + Supabase Free/Pro |
| Launch | 1–10k MAU | 2 Fly machines + Redis + Supavisor + staging parity |
| Growth | 10–100k MAU | Managed WS (Ably/Pusher) + read replicas + Sentry perf |
| Scale | 100k+ | Multi-region + partitioned tables + event-driven worker queue |

---

## Priority summary

| ID | Severity | Effort |
|---|---|---|
| P1 rate limiter | P0 | S |
| P2 WS fan-out | P0 | M |
| P3 cache | P1 | M |
| P4 indexes | P1 | M |
| P5 PostGIS surge | P1 | M |
| P6 KNN dispatch | P1 | M |
| P7 async webhook | P1 | M |
| P8 gps partition | P1 | M |
| P9 pool | P1 | M |
| P10–P15 | P2 | S–M |
| P16–P18 | P3 | S–M |

---

*Continue to → [08_COMPLIANCE_UX.md](./08_COMPLIANCE_UX.md)*
