# A3 — Transmission, compression, caching & SaaS capacity

**Lane:** A3 · **Model:** fable / general-purpose · **Returned:** 2026-09-24 ~14:31 UTC · **Orchestrator note:** lane output pasted verbatim below; only this header was added. The filled §5 capacity table is duplicated into `capacity-table.md`. Every vendor-doc WebFetch was blocked by the egress proxy, so all vendor limits are INFERRED from search snippets. No load test has ever been run against the production shape — every 10× figure is arithmetic. The lane counts 45 loops in `backend/core/background_loop_registry.py` where CLAUDE.md says 42; the registry file is the source of truth (VERIFIED by lane), so CLAUDE.md's count is stale.

---

Evidence labels: **VERIFIED** = repo path:line read in this session or primary vendor page fetched; **INFERRED** = search-snippet or arithmetic from code shape; **ASSUMED**; **UNKNOWN**. Every vendor-doc WebFetch to supabase.com, docs.stripe.com, docs.fly.io, uvicorn.dev was **blocked by the egress proxy** (uvicorn.org: DNS EAI_AGAIN) — vendor numbers below come from search snippets only and are INFERRED, never VERIFIED. No load test has ever run against the production shape (`docs/runbooks/capacity-scaling.md` §8, VERIFIED) — every 10× figure is arithmetic.

## (a) §5 capacity table — see `capacity-table.md`

## (b) Finding cards (7; de-duped against ACTION_ITEMS.md by grep — E1, C5, C50, C104, D7, C97 cited rather than re-filed)

### A3-001 — Every API process still spawns all 45 background loops (process-role split exists but is not enabled)
- Hierarchy: L2 Platform reliability › L3 Background jobs › L4 loop placement › L5 multi-replica DB polling
- Severity: **HIGH**   Priority score: S×B×L = 4×4×5 = 80
- Status: VERIFIED   Existing item: related to C5 (Railway standby) and the 2026-08-26 DB-optimization audit item #4; the *placement registry* is new since that audit and has no ACTION_ITEMS entry — **new**
- Adversary: flaky network / cost auditor (every replica × every loop × the standby hits one non-autoscaling DB)
- Evidence: `backend/core/background_loop_registry.py:21,86` (`resolve_process_role` defaults to `"all"`), `:113-114` (`role=="all"` → spawn everything), registry counts 15 `api` + 3 `worker_wave1` + 27 `deferred` = 45; `backend/fly.toml` `[env]` has **no** `SPINR_PROCESS_ROLE`; `backend/core/lifespan.py:182-186,307`; runbook §2 says "18 background loops", DB audit `:267` measured 40 with 14 lacking leader locks and 4 concurrent surge-loop writers.
- What happens: DB load has a fixed floor per running process (2 warm × 2 workers = 4 processes, 16 at full burst, plus Railway's 4) regardless of user count; burst machines woken for *user* traffic add *loop* traffic to the same DB tier, which is the opposite of what a burst pool should do.
- Root cause: the role/allowlist mechanism was built but the deploy config never set it, so the safe default ("all") is production behaviour.
- Recommendation: set `SPINR_PROCESS_ROLE=api` in `fly.toml [env]` for `app`/`burst` and run one `worker` process (or role `all` on a single dedicated machine). Alternative considered: adding leader locks to the 14 unlocked loops — rejected because locks fail open on Redis errors (`surge_engine.py:483`, `offer_expiry_reaper.py:146`) so they don't remove the multiplier, only mask it.
- Blast radius: all 45 loops; `loop_watchdog` registration (`lifespan.py:804-861`) already filters by role so alerts stay consistent; Railway standby must get the same env (parity requirement, `railway-fly-failover.md`).
- Rollout: env-only, no code; flip one machine at a time. Rollback: unset env → role "all" (config revert, no deploy of code).
- Verification to close: `flyctl logs | grep "Background loop process role"` shows `api` on app machines; DB audit's top-5 loop-poll query counts drop by ≥ (processes−1)/processes.

### A3-002 — GPS ping hot path performs ~5–6 Redis round-trips per ping; Redis ops/s is the first 10×-driver break point
- Hierarchy: L2 Dispatch › L3 Live location › L4 WS location fan-out › L5 10× drivers on trip
- Severity: **HIGH**   Priority score: 4×4×3 = 48
- Status: INFERRED (arithmetic from verified code shape; no profiler)   Existing item: new (ACTION_ITEMS has no Redis-ops-budget item; grep "fan-out"/"pub/sub" returns only test-coverage entries)
- Adversary: competitor / flaky network (fleet-wide WS lag during a Saturday peak)
- Evidence: per ping — `routes/websocket.py:1185` (`resolve_active_rides_cached`, Redis GET), `utils/location_integrity.py:190` (GET+SET), `utils/location_write_gate.py:104-146` (GET/SET NX, 3 s marker gate), `socket_manager.py:574-596` (SET 60 s TTL), `utils/ws_pubsub.py:197-229` (Lua durable publish or PUBLISH), plus admin throttle (`socket_manager.py:460-483`). Cadence: driver-app 2 s on trip / 4 s idle (`driver-app/hooks/useDriverDashboard.ts:78-89`); backend comments assume 1 Hz (`websocket.py:109,826`).
- What happens: at 100 concurrent on-trip drivers @ 0.5 Hz ≈ 50 pings/s ≈ 300 Redis ops/s — fine. At 1,000 drivers (10×) ≈ 500 pings/s ≈ 2.5–3k Redis ops/s plus PUBLISH fan-out to every replica's subscriber (`ws_pubsub.py:333` consumer parses every message on every machine, 8 machines → 8× JSON decode per publish). Bandwidth is trivial (see (c)); the cost is round-trip latency on the <150 ms location-write SLA and consumer-loop CPU on every replica.
- Root cause: each safeguard added its own Redis key independently; nothing batches them.
- Recommendation: one Lua script per ping returning (active_rides, integrity_prev, gate_ok) and doing SET/PUBLISH atomically; unicast-only publishes could be routed by key-hash to a per-replica channel so consumers skip messages for connections they don't hold. Alternative: keep as-is and scale Redis vertically — rejected because the replica-side consumer decode cost scales with machines × publishes, which a bigger Redis doesn't fix.
- Blast radius: every WS location consumer (rider marker, admin map, breadcrumbs), `test_websocket*` suites, `location_write_gate` shadow metrics.
- Rollout: flag `ws_location_lua_path_enabled` in `app_settings`; shadow-compare outcomes. Rollback: flag off (no data written differently).
- Verification to close: Locust Scenario B on staging (E1) with 150 driver bots at 0.5 Hz; `spinr_ws_fanout_duration_ms` P95 <100 ms and Redis `INFO commandstats` ops/ping ≤2.

### A3-003 — No response compression at the origin; admin list/export bodies up to 10k full ride rows are sent as raw JSON
- Hierarchy: L2 Admin/Ops › L3 Lists & exports › L4 transfer size › L5 large export on mobile hotspot
- Severity: **MEDIUM**   Priority score: 2×3×4 = 24
- Status: VERIFIED (absence) / INFERRED (Cloudflare compresses proxied HTTP)   Existing item: new (grep gzip/brotli/compress in ACTION_ITEMS: only an unrelated CI mention at :25276)
- Adversary: cost auditor (Supabase→Fly→client egress ×2), abusive admin session (10k-row export hammering)
- Evidence: no `GZipMiddleware`/brotli in `backend/server.py` or `backend/core/middleware.py` (grep, VERIFIED); `backend/fly.toml` has no compression handler; API CNAME is Cloudflare-**proxied** (`docs/runbooks/railway-fly-failover.md:14`) so Cloudflare likely compresses HTTP to the browser (INFERRED — fly/cloudflare docs blocked), but **origin→Cloudflare and mobile-app→Fly direct paths stay uncompressed**, and WebSocket frames are never compressed by Cloudflare. Three largest bodies by code reading: (1) `GET /admin/rides/export` — `get_rows("rides", …, limit=_EXPORT_MAX_ROWS=10_000)` returned as `{"rides": out, "total_count": …}` JSON (`routes/admin/rides.py:288,291,322,344` → INFERRED ~10–25 MB at 1–2.5 KB/row, full `select *`); (2) `GET /admin/export/rides` `limit` ≤10,000 default 1,000 (`rides.py:3031-3047`); (3) `GET /admin/export/users` `limit` ≤5,000 (`routes/admin/users.py:456,475-489`). Honourable mentions: `routes/admin/messaging.py:59` reads 50,000 ride rows (single column) to build a recipient set; `routes/admin/subscriptions.py:192` `limit=50000`; `routes/admin/drivers.py:1525,1573` 5k/10k driver rows into one list response.
- What happens: an admin exporting a quarter pulls tens of MB uncompressed; on the mobile apps every JSON list (ride history, earnings) is uncompressed end-to-end.
- Root cause: compression was assumed to be the CDN's job; nobody checked the mobile→API path bypasses the assumption (apps hit `api-spinr.spinr.ca`, which is proxied — but only if the app URL is the proxied hostname; **not verified** which hostname the apps' `EXPO_PUBLIC_BACKEND_URL` points to).
- Recommendation: add Starlette `GZipMiddleware(minimum_size=1024)` (stdlib, zero deps) after the security-headers middleware; convert the 10k export to a streaming CSV/NDJSON. Alternative: brotli middleware — rejected for now (extra C dependency in a `--require-hashes` locked image; gzip gets ~80% of the win for JSON).
- Blast radius: every HTTP response; check `middleware.py:946,998` Cache-Control/Accept headers and any test asserting raw `Content-Length`.
- Rollout: additive middleware, ship dark behind `ENABLE_GZIP` env; clients already send `Accept-Encoding`. Rollback: env off.
- Verification to close: `curl -H 'Accept-Encoding: gzip' -sI …/api/admin/rides/export` shows `Content-Encoding: gzip`; `npm run build` for admin-dashboard unaffected (no change there).

### A3-004 — Maps daily budget breaker is a $5/day cliff with no early-warning tripwire; fails open on Redis error
- Hierarchy: L2 Rider booking › L3 Address search / estimates › L4 Maps spend guard › L5 mid-day budget exhaustion at 10× rides
- Severity: **MEDIUM**   Priority score: 3×3×3 = 27
- Status: VERIFIED   Existing item: C104 (atomicity, closed by `reserve_budget`) — the *tripwire* gap is new
- Adversary: fraudster (autocomplete-spam to exhaust the budget and blind riders), regulator (WAV/accessibility flows losing geocode)
- Evidence: `backend/utils/maps_budget.py:115-116` (`MAPS_DAILY_BUDGET_USD` default 5.0), `:5-6` (503 when exceeded), `:15-17` (Redis outage → allow), `:253-334` (atomic reserve); `billing-usage-monitor.yml:17-25` states GCP budget alerts are not implemented.
- What happens: on the first busy day the breaker opens at lunchtime; autocomplete/geocode return 503 for the rest of the UTC day; fare estimates silently fall back to haversine (undercharge risk the 3.5 s Directions wait exists to prevent).
- Root cause: breaker sized for cost safety, never re-sized for growth; no 70% warn.
- Recommendation: emit `spinr_maps_budget_spent_ratio` gauge and alert at 0.7 via the existing `capacity_watchdog` channel; move budget to `app_settings` so it can be raised without redeploy. Alternative: remove the breaker and rely on GCP budgets — rejected (GCP alerting is next-cycle, not seconds).
- Blast radius: `routes/maps_proxy` consumers, `ai/tools_booking.py`, `utils/maps_eta.py` fallback.
- Rollout: additive gauge + setting. Rollback: setting revert.
- Verification to close: unit test asserting warn at 70%; staging run with budget=$0.10 shows alert before 503.

### A3-005 — Capacity runbook is stale against `fly.toml` (suspend vs off, 1 GB vs 4 GB/2 GB, 18 vs 45 loops); Railway runs 4 uvicorn workers vs Fly 2
- Hierarchy: L2 Ops › L3 Runbooks › L4 capacity-scaling › L5 incident response reads wrong numbers
- Severity: **MEDIUM**   Priority score: 2×3×4 = 24
- Status: VERIFIED   Existing item: C5 is closed (Railway deploy fixed) but this parity/drift point is new
- Adversary: on-call at 2 a.m. following a runbook that describes a fleet that no longer exists
- Evidence: `docs/runbooks/capacity-scaling.md` §2 ("suspended 6 (auto_stop_machines = "suspend")", "1 GB VM", "18 background loops") vs `backend/fly.toml:11,50,85-95` (`auto_stop_machines="off"`, 4 GB/2 GB, note "Scale-out only during observation"); `railway.json:8` `UVICORN_WORKERS:-4` vs `fly.toml:29` `"2"` → Railway's per-process DB pool math (§4 table) is 2× what the runbook computes, on the same Supabase tier.
- What happens: during a DB-saturation incident the standby, if failed over to, pushes 4×64 threads/machine instead of 2×64.
- Root cause: fleet was re-sized (mixed fleet, `docs/runbooks/fly-mixed-fleet.md`) without a runbook pass; the CI/CD reviewer agent still cites the pre-fix C5 state too (`.claude/agents/spinr-cicd-infra-reviewer.md` §4).
- Recommendation: update runbook §2/§4 and `railway.json` `UVICORN_WORKERS` to 2 (parity). Alternative: leave Railway at 4 for "more capacity" — rejected; standby must be a faithful replica or fail-over behaviour is unverifiable (C1).
- Blast radius: docs + one JSON value; standby-parity-monitor.yml compares deploy SHA, not env — won't catch it.
- Rollout: doc + config. Rollback: revert JSON.
- Verification to close: `standby-parity-monitor.yml` extended to diff `UVICORN_WORKERS`; runbook numbers match `fly.toml`.

### A3-006 — No repo record of the Supabase compute tier, Redis provider/plan, Sentry quota, Vercel or EAS plan → capacity table cannot be VERIFIED from the repo
- Hierarchy: L2 Ops › L3 Vendor inventory › L4 plan-of-record › L5 10× planning
- Severity: **MEDIUM**   Priority score: 2×4×3 = 24
- Status: VERIFIED (absence)   Existing item: `docs/runbooks/renewal-calendar.md` rows are all "TBD" (:44-57) — a stuck open item, not new; reported here as *why* it's stuck: no owner has dashboard access from any agent session (C99 pattern)
- Adversary: auditor ("what tier are you on?" → no answer in writing)
- Evidence: `renewal-calendar.md:44-57`; `capacity-scaling.md` §5 "Confirm … they change, so do not plan capacity from memory"; `redis-down.md:38`; no `vendor-inventory.md` rows matched grep.
- Recommendation: a human with dashboard access fills one table (tier, max_connections, pooler size, Redis plan/maxmemory, Sentry quota, Vercel/EAS plan) into `capacity-scaling.md` §5 and the renewal calendar. Alternative: automate via Management APIs — partially exists (`supabase-capacity-monitor.yml`) but is gated on an unprovisioned token.
- Blast radius: docs only. Rollout/Rollback: n/a.
- Verification to close: each §5 row has a VERIFIED source.

### A3-007 — WS permessage-deflate is negotiable server-side by uvicorn default but never asserted or measured; location JSON carries ~40% redundant bytes per frame
- Hierarchy: L2 Dispatch › L3 Live location › L4 WS encoding › L5 rural cellular
- Severity: **LOW**   Priority score: 1×3×3 = 9
- Status: INFERRED   Existing item: new
- Adversary: flaky network (rural SK cellular)
- Evidence: `uvicorn[standard]==0.52.4`, `websockets==15.0.1` (`backend/requirements-locked.txt:3535,3707`); no `--ws-per-message-deflate`/`ws_max_size` flag in `fly.toml:33-34`/`railway.json:8`; uvicorn default `ws_per_message_deflate=True` for the `websockets` impl (search snippet, uvicorn docs blocked — INFERRED); no compression reference in either app's socket code (grep, VERIFIED absence). Payload shape `routes/websocket.py:1200-1224`: `type`(22 B literal) + `driver_id` UUID (36 B) + ISO `captured_at` (32 B) + `ride_id` UUID on the rider copy.
- What happens: whether frames are compressed depends on whether React Native's WebSocket (OkHttp on Android, NSURLSession on iOS) offers the extension — unknown; either way the ~265 B rider frame could be ~120 B with short keys/epoch-ms.
- Recommendation: **assess** — log negotiated extensions once per connection (`websocket.scope["extensions"]`) into a metric before deciding; do not change the wire format during live testing. Alternative: adopt deflate explicitly — rejected pending memory measurement (uvicorn issue #1862 reports per-connection zlib memory; at 6,000 WS/machine that matters).
- Blast radius: all WS clients; `test_websocket*`.
- Rollout: metric only. Rollback: n/a.
- Verification to close: metric shows % of connections with `permessage-deflate`.

## (c) WS payload arithmetic (INFERRED, from verified code shape)

Per on-trip driver at driver-app cadence 2 s (`useDriverDashboard.ts:86,88` → 30 pings/min; backend assumes 60/min at `websocket.py:109`):
- Inbound ping ≈ 170 B JSON + 8 B masked frame ≈ **5.3 KB/min** (10.7 KB/min @1 Hz)
- Rider fan-out (`websocket.py:1200-1224`, +`ride_id`,`eta_seconds`) ≈ 265 B → **8 KB/min** per rider on that ride
- Redis: PUBLISH envelope ≈ 330 B + location SET ≈ 90 B → **~12.6 KB/min**; consumer decode on **every** replica (8 machines → 8×)
- Heartbeat `HEARTBEAT_INTERVAL=10` (`websocket.py:286`) → 6 ping/pong pairs ≈ **0.4 KB/min**
- Admin map: throttled per driver (`socket_manager.py:460-483`), bounded by admin count
- **Per driver-minute on trip ≈ 26 KB (≈ 50 KB @1 Hz)**; idle ≈ 3–4 KB/min. 100 drivers ≈ 2.6 MB/min (43 KB/s) — negligible; 1,000 drivers (10×) ≈ 26 MB/min ≈ 430 KB/s network, **but 500 pings/s × ~6 Redis ops = ~3k Redis ops/s + 8 replicas × 500 msg/s pub/sub decode** — Redis/consumer CPU binds long before bandwidth. Message cap `WS_MAX_MESSAGE_SIZE=64 KB`, 30 msg/s per socket (`websocket.py:290-291`) — location batches of up to 500 points (`driver-app/utils/tripLocationOutbox.ts:60`) at ~100 B/point ≈ 50 KB fit under the cap with little margin.

## (c′) Caching findings (VERIFIED)
- `app_settings`: in-process TTL cache in `backend/settings_loader.py:20-27` (`_SETTINGS_TTL`); WS path refreshes Maps key ≤ every 60 s (`websocket.py:1190-1198`). Good.
- Row cache: Redis read-through for users/drivers, **30 s TTL** (`repositories/_base.py:636-638`, `driver_repo.py:62,87`, `auth_repo.py:56`).
- Fare: `FARE_CACHE_TTL_SECONDS=300` per grid cell (`core/config.py:230`); surge status 30 s (`surge_engine.py:359`); live route 30 s / nav steps 300 s (`route_distance.py:769,883`); LMS 300 s; analytics cancellation-reasons cached (D7 closed).
- **Not cached**: `service_areas` full active list is re-read from PostgREST on every fare calc (`services/fare_service.py:524`, `features.py:702,869,968` `limit=100`) and `fare_configs` (`features.py:638,868`) — polygons rarely change; a 60 s Redis/in-process cache removes 2 round-trips per estimate on the 300 ms SLA path. `lru_cache` only on `build_info.py:40`; `Cache-Control`/ETag only referenced in CORS header lists (`middleware.py:946,998`), no response uses them.

## (d) industry-stack-benchmark.md verification

| Claim | Verdict | Note |
|---|---|---|
| Uber Go/Java, gRPC/Thrift, QUIC edge, Cadence, Kafka/Flink, Docstore, H3, M3/Jaeger, RIBs | **Unverifiable** | uber.com blocked; no search budget left after vendor limits (6 used). Remains INFERRED as the file says |
| Lyft Envoy / Envoy Mobile, Go+Python, Protobuf, Flyte | **Unverifiable** | lyft.com blocked |
| Grab Grab-Kit, Kafka, GrabMaps | **Unverifiable** | assets.grab.com blocked |
| "Spinr today" column: FastAPI monolith, REST/JSON+WS JSON, Cloudflare→Fly/Railway no mesh, asyncio loops+outbox+leader locks, Redis pub/sub+Postgres outbox, Supabase Postgres, H3/geohash/OSRM/Maps, `app_settings` flags, Prometheus-style+Sentry, tracing deferred (ADR-014), Expo (ADR-002) | **Confirmed** | all VERIFIED in this session (`fly.toml`, `ws_pubsub.py`, `lifespan.py`, `background_loop_registry.py`, `maps_budget.py`, `docs/adr/`) |
| §3 "Config/flag platform — MODIFY" | **Confirmed + sharpen** | `app_settings` flags exist; role placement (`SPINR_PROCESS_ROLE`) is env-only, not a flag — A3-001 |
| §3 "Kafka HOLD / mesh HOLD / own maps HOLD" | **Confirmed** | 10× arithmetic shows Redis ops and DB round-trips bind, not event volume |
| §2 "QUIC at the edge (Uber)" as something to consider for Spinr | **Corrected** | Fly proxy has no h3 handler (INFERRED, fly.io community/docs snippets); only Cloudflare-proxied HTTP could get HTTP/3, and WS/mobile long-lived sockets don't benefit |

## (e) Research table (§6 Transmission)

| Technique | Verdict | Reason | Source |
|---|---|---|---|
| HTTP/3 / QUIC at edge | **hold** | Fly proxy exposes only `http`/`tls`/`pg_tls`/`proxy_proto` handlers, no h3; Cloudflare could serve HTTP/3 to browsers only, and the latency-critical path is a long-lived WS, not request setup | fly.io docs/community snippets (INFERRED; docs.fly.io blocked); `fly.toml:60-70` |
| Brotli for JSON | **trial (gzip first)** | Origin sends nothing compressed today (A3-003); Starlette gzip is dependency-free; brotli needs a C wheel in a hash-locked image — trial after gzip metrics exist | `server.py`/`middleware.py` grep; `requirements-locked.txt` |
| WS permessage-deflate | **assess** | uvicorn default already negotiates it (INFERRED); unknown whether RN clients accept; per-connection zlib memory at 6k conns/machine unmeasured | uvicorn settings snippet; uvicorn issue #1862; A3-007 |
| Quantized / delta location encoding | **assess** | Frame is ~265 B, dominated by UUIDs/ISO strings; short keys + epoch-ms + 5-decimal fixed-point gets ~50% without binary; bandwidth is not the 10× bind (Redis ops are) so value is rural-cellular robustness, not cost | `websocket.py:1200-1224`; arithmetic in (c) |
| Adaptive GPS sampling by speed/state | **adopt (already partial)** | driver-app already varies cadence by ride state (2/4/8/10 s, `useDriverDashboard.ts:72-89`); missing speed-based backoff at standstill (still 2 s on trip when stopped) and battery-state hook; backend `MARKER_WRITE_INTERVAL_S=3.0` gate already decouples DB writes | `driver-app/hooks/useDriverDashboard.ts:72-89`; `utils/location_write_gate.py:104` |

## (f) Steelman (what the current design gets right)
- Connection-count concurrency on Fly is the right unit for a WS-heavy app; pool size and limits ship together deliberately (`capacity-scaling.md` §2).
- DB thread pool is bounded with a queue (`BoundedExecutor`) and a breaker, so overload fails as queueing, not a DB stampede — and the runbook explains *why* 64 not 96.
- Hot GPS path is DB-free (3 s marker gate, 5 s active-ride cache, ETA refreshed off-loop `websocket.py:1229-1250`), respecting the <150 ms write SLA.
- Non-durable location publishes don't evict ride events from the 50-entry replay outbox (`websocket.py:1255-1261`).
- Maps budget breaker stops spend in seconds and is atomic (C104); GCP alerting would be next-cycle.
- Loop placement registry with role/allowlist is already built — enabling it is config, not engineering.
- 3.5 s fare-estimate wait is an accepted, documented SLA exception; not re-litigated.

## NOT VERIFIED / blocked
- **Blocked hosts (WebFetch):** supabase.com, docs.stripe.com, docs.fly.io (and fly.io redirect), uvicorn.dev; www.uvicorn.org DNS failure. lyft.com / uber.com / grab.com not attempted (pre-declared blocked). All vendor limits above are INFERRED from search snippets.
- Not verified: actual Supabase compute tier, max_connections, Supavisor pool size; Redis provider, maxmemory, eviction policy; Sentry/Vercel/EAS plans; which hostname the mobile apps call (proxied vs direct) and therefore whether Cloudflare compresses their traffic; whether RN WebSocket negotiates permessage-deflate; real CPU/RSS at 750 connections; the ~10–25 MB export size (row-size estimate); current live driver count (10× multiplier applied to code-shape ratios, not a measured base).
- Web searches used: 6/6. Sentry/Stripe connectors not used. No files written.
