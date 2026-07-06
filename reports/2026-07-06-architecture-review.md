# Spinr — Engineering Director Code & Architecture Review
**Date:** 2026-07-06 · **Scope:** read-only teardown of backend, payments, dispatch, security, telemetry, and the mobile/admin surfaces · **Benchmark:** Uber / Lyft

> **Framing.** Spinr is already unusually mature for pre-launch. The six sprint P0s and the entire P1/P2/P3 audit backlog are shipped (HttpOnly admin tokens, atomic payment guard, GPS-OOM Postgres fn, refresh-token reuse cascade, RLS-by-default migrations, PII scrubbing). This review is deliberately the **next tier down** — net-new issues *beyond* what the trackers already close. Where the code is genuinely strong, I say so, so the signal-to-noise stays high.

---

## 🚨 Critical Issues & Security Flaws

| # | Finding | Location | Why it matters |
|---|---|---|---|
| C1 | **Driver ledger + tax receipt are skipped on the PaymentSheet/webhook settlement path.** `payment_intent.succeeded` and `/payments/confirm` only stamp `payment_status='paid'` + `paid_at`; they never call `record_payment_event` or `send_ride_receipt`. Only `process-payment → settle_card` credits the driver. | `routes/webhooks.py:561-637`, `routes/payments.py:466-472` vs `services/payment_service.py:770` | If any client settles via PaymentSheet instead of process-payment, the **driver is never paid and no GST receipt is issued** in a 0%-commission model. Single biggest money-robustness gap. **Verify which path is canonical, then unify crediting into one settlement function both entrypoints call.** |
| C2 | **Batch-timeout re-dispatches an already-accepted ride.** `_batch_offer_timeout_handler` checks `status==SEARCHING` at 1600, then does many awaits before re-calling `match_driver_to_ride` at 1685. A driver `accept` landing in that window flips the ride to `driver_accepted`; line 1685 then claims fresh drivers and sends **phantom offers** on a live ride. | `routes/rides.py:1600` vs `1685`; accept CAS at `routes/drivers.py:4122` | Violates the ride-state invariant ("dispatch a non-`searching` ride = contract violation"). Re-check status atomically immediately before the re-dispatch call, or make `match_driver_to_ride` itself refuse non-`searching` rides. |
| C3 | **Refunds and downward tip corrections desync the ledger.** `charge.refunded` reverses only the rider charge — never `driver_earnings`, a compensating `financial_events` row, or GST. `_tip_ride_update` credits the driver only on tip *increase* (`if tip_delta > 0`). | `routes/webhooks.py:800-810`, `services/payment_service.py:98` | In a 100%-to-driver model, a refund leaves the driver overpaid and **T4A/GST remittance wrong**. Partial refunds store a flat amount with no tax proration. Regulatory (CRA) exposure. |
| C4 | **Surge line-item is misleading and under-delivers the advertised multiplier.** Surge is applied only to distance+time (`base_fare`/`booking_fee` unsurged), then the receipt emits a `"Surge (1.5×)"` line with `amount: None` on top of a "Ride fare" that already has surge baked in. Rider never sees the surge dollar figure; effective multiplier ≠ advertised. | `services/fare_service.py:204-269` | Transparency/regulatory risk — CLAUDE.md forbids hidden/misleading line items and SK requires disclosed charges. Uber/Lyft itemize surge as an explicit dollar line. Itemize the surge delta as a real dollar amount. |
| C5 | **IP rate limits are bypassable via `X-Forwarded-For` spoofing, and there is no per-phone OTP throttle → SMS-bombing + Twilio cost abuse.** SlowAPI's `get_ipaddr` trusts the leftmost client-supplied XFF; no `TrustedHost`/proxy-hop validation. `send-otp` gates only `3/min per IP`; `get_phone_based_key` exists but is wired into nothing. | `utils/rate_limiter.py:102,139`, `routes/auth.py:229-231`, admin login `admin/auth.py:307` | One spoofed header floods any victim number with SMS and defeats admin-login throttling. Pin trusted proxy count and add a per-destination-number send cap. |
| C6 | **Blocking, un-threaded Stripe calls in the settlement hot path.** `charge_ride`/`authorize_ride`/`capture_ride` call Stripe synchronously (not wrapped in `asyncio.to_thread`), inside the request path via `settle_card`. Each settlement stalls the event loop for the full ~300–800 ms round-trip. | `utils/stripe_charge.py:217,223,532,727` (contrast the correct `to_thread` at `payments.py:225`) | Directly threatens the **settlement < 1 s SLA** and starves all co-located requests under load. |

**Also verified strong (no action):** no SQL injection (all param-wrapped supabase-py), SSRF guarded (SNS host validation, fixed-host maps proxy), constant-time OTP compare, bcrypt+TOTP admin lockout, refresh rotation/reuse cascade, RLS-by-default on new user-data tables. **Watch:** `REVIEW_LOGIN_ACCOUNTS` fixed-code bypass is production-live (`config.py:277`) — ensure it's empty outside active store review.

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**The envelope is well designed; the wiring underneath fragments it.**

- **Correlation is severed on the highest-value surfaces.** `request_id` is bound only through loguru `contextualize()`, and the JSON sink + Sentry ERROR bridge are loguru-only — but payments, auth-dependency, webhooks, dispatch-service, and **all 16 background loops** use stdlib `logging.getLogger`. Those lines carry **no request_id, no JSON, and land in the default root handler** (INFO dropped). A support ticket quoting `X-Request-ID` cannot be grepped against the exact code paths that matter most. **Highest-leverage single fix:** add a stdlib→loguru `InterceptHandler` so every module inherits `request_id` + JSON + the Sentry bridge. (`core/middleware.py:190`, `server.py:394-470`, `utils/audit_logger.py:8`)
- **Raw errors can leak to end users.** The exception handler sanitizes only `status >= 500`; any `HTTPException(400/422, detail=<technical text>)` reaches the user verbatim, and the client surfaces `data.detail` first. Raw **Stripe decline text** is pushed straight into a rider push-notification. Validation arrays are concatenated into `"body -> phone: field required"`-style jargon. (`utils/error_handling.py:737`, `routes/webhooks.py:685`, `shared/api/client.ts:351-357`)
- **Auth control fails *open*.** A malformed `last_activity_at` on a staff token → `logger.warning("…letting through")` and the idle-timeout check is skipped. This is a warning-and-continue on an auth control — CLAUDE.md forbids exactly this, and it should fail **closed**. (`dependencies/__init__.py:326`)
- **DB/payment failures softened to `warning` with bare `{e}`** (no `exc_info`, no `DatabaseError.details['original']`): fare-snapshot save (`rides.py:3173`), attribution writes (`rides.py:2187,4974`), driver minimal-field retry (`drivers.py:5148`), doc supersede/flag (`documents.py:215,234`), payment-failure line missing `event_id`/`request_id` (`webhooks.py:678`).
- **Metrics/tracing gaps.** All 7 taxonomy metrics *are* emitted correctly (good) — but they're **per-process in-memory** (lost on restart; gauges race across replicas), there is **no distributed tracing** (`X-Trace-ID` is just the echoed request_id — no OTEL, no spans into Supabase/Stripe/Redis), and **no SLO/burn-rate alert rules** wire the correctly-named metrics to paging, so the CLAUDE.md SLA table is unenforced. Degradation paths (Redis→in-proc, ETA OSRM→Google→haversine, fare-cache miss) log warnings with **no counters** — invisible on dashboards.

**Strong (no action):** DB circuit breaker, Redis in-proc fallback, ETA multi-provider fallback, client offline queue, 503-retry + 401-refresh interceptors, SOS exempted from 401→logout, Sentry PII scrubber (phone/email/GPS redaction).

---

## 🐢 Performance Bottlenecks & Optimizations

- **Dispatch candidate query pulls `columns="*"` for up to 500 driver rows — including encrypted PII — every dispatch**, just to compute haversine. Select only `id, lat, lng, rating, is_available`. (`routes/rides.py:795,805-811`)
- **Serial per-driver awaits everywhere on the hot path** (no `asyncio.gather`): claim loop does 2 DB round-trips per candidate (`claim_driver_atomic` + full `get_driver_by_id` re-read) one-at-a-time (`rides.py:1146-1160`); batch-timeout miss-streak/acceptance-rate/availability/period/WS writes all sequential (`rides.py:1633-1673`); loser cleanup sequential (`drivers.py:4231-4243`).
- **External Distance-Matrix ETA awaited inline** in the offer path (`rides.py:1130`) — put it behind a cache/pre-warm; it's on the < 2 s dispatch clock.
- **3 Redis round-trips per durable WS event** (`INCR` + 3-op outbox pipeline + `PUBLISH`) on the < 100 ms fan-out path (`utils/ws_pubsub.py:186-206`).
- **Fare estimate does sequential Supabase reads** (`service_areas` → `fare_configs` → `area_fees`) plus O(areas) point-in-polygon run twice — chatty against the < 300 ms SLA. (`features.py:936`, `fare_service.py`)
- **Blocking Stripe calls** (C6) — the biggest single event-loop staller.
- **Slow memory leaks:** `_admin_loc_last` dict never evicts (one entry per driver ever seen, `socket_manager.py:55,391`); 500-row candidate cap silently truncates in dense areas with only a warning.

---

## 💡 Tech Stack & Architecture Recommendations

| Gap | Why it hurts | Recommended fill |
|---|---|---|
| **No durable job/queue system** — Twilio/Stripe/push run `asyncio.create_task` fire-and-forget; 16 loops poll the DB on every replica. | Side-effects are lost on crash/restart; no at-least-once guarantee; payment-retry/receipt delivery is best-effort. | **Temporal** (workflow durability — Uber's own Cadence lineage) for settlement/receipt/payment-retry; **arq** or Celery+Redis for SMS/push. Move insurance-period transitions onto a durable log. |
| **No distributed tracing** | A dispatch spanning WS→DB→Stripe→Twilio can't be traced against P95 SLAs; correlation dies at the process boundary. | **OpenTelemetry SDK** → Tempo/Honeycomb; propagate trace context into Supabase/Stripe/Redis calls. |
| **No feature-flag / kill-switch system** — surge cap, dispatch radius, kill-switches are code/DB constants. | No runtime kill switch for a bad dispatch or surge change; every toggle is a deploy. | **Flagsmith / Unleash** (self-hostable, Canadian-residency-friendly). |
| **No geospatial hot layer** — flat `drivers` table + SQL bounding box + Python haversine. A PostGIS `match_and_claim_driver` RPC with `FOR UPDATE SKIP LOCKED` already exists but is **dead code** because `update_driver_location` never populates the geo column. | Won't hold the < 150 ms location-write / < 2 s dispatch SLAs at city scale; greedy per-ride matching causes thundering-herd contention on popular drivers. | **Redis GEO** (`GEOADD`/`GEOSEARCH`) hot store + **h3-py** cell bucketing (Uber's H3). *First, just wire the existing PostGIS RPC by populating the geo column* — the atomic primitive is already written. |
| **No event streaming** — dispatch/location on DB-polling loops + Redis pub/sub. | Single fleet-wide pub/sub channel is O(replicas × all traffic); no replayable event log. | **Redpanda/Kafka** ride + insurance-period event log; consumers replace polling loops. |
| **No real connection pooling** — `psycopg2-binary`, no pool; all runtime DB via supabase-py HTTP. | Postgrest HTTP per call adds latency and caps throughput. | Configure **Supavisor/PgBouncer**; move `migrate.py` to **psycopg3**. |
| **No monorepo tooling** — 5 surfaces linked via `file:../shared` + `postinstall` hacks. | Fragile installs, no shared build cache, drift. | **pnpm workspaces + Turborepo**. |

**Dependency risk:** manifest is a **major ahead of documented baselines** — `expo ~55` / `react-native 0.85` / `react 19` (CLAUDE.md says Expo SDK 54), admin `typescript ^6` / `@types/node ^25` / `next ^16` / `vitest ^4`, `uuid ^14`. Redundant Firebase (native + JS SDK both present). ESLint split (rider `^8` EOL vs `^9`). Backend drags `anthropic`+`openai`+deprecated `google-generativeai 0.8.6` and pyiceberg→pandas/numpy/pyarrow into a ride backend. **Pin to documented baselines and prune.**

---

## 🛠️ Maintainability & Code Smells

- **Two divergent timeout handlers coexist** — legacy per-driver `_offer_timeout_handler` (expects `driver_assigned`, 30 s) vs the actually-spawned batch handler (expects `searching`, 15 s). The dead one encodes an obsolete state model — delete it. (`rides.py:1391,1400`)
- **`DispatchService.find_candidate_drivers` diverges from the live route path** — no geo bounding box, `limit=500` province-wide. If ever adopted it reintroduces the "nearest driver in row 501 → false no-drivers" bug. (`dispatch_service.py:300-333`)
- **Two divergent HTTP stacks in `shared/api/`** — `client.ts` (axios, in-memory token, 401-refresh dedupe) vs `cachedClient.ts` (raw fetch, SecureStore token). Consolidate to one. (`cachedClient.ts:38-58`)
- **Migration prefix collisions continue** (duplicate `200_`); numbering has advanced to 207 while CLAUDE.md still says "next free slot 145." Full-filename keying tolerates it, but the doc drift misleads authors — update the note.
- Redundant Firebase init paths; dead client field `exception_type` still parsed (`client.ts:277`).

---

## 🧪 Testing & QA (missing edge cases)

- **Per-file coverage floors are still not enforced** (ACTION_ITEMS A1 open): CLAUDE.md mandates ≥90% payments/fare, ≥80% rides/dispatch; `pytest.ini` global floor is 60%. Ratchet per-path `--fail-under` in CI.
- **Untested critical branches surfaced above:** refund → driver-earnings/GST reversal (C3), downward tip correction (C3), PaymentSheet-vs-process-payment crediting parity (C1), batch-timeout accept-race (C2), surge dollar-itemization (C4), per-phone OTP throttle (C5 — `get_phone_based_key` has tests but isn't wired to the route).
- **No SLO/alerting artifacts** in-repo despite correctly-named metrics — add burn-rate alert rules so the SLA table is enforced, not aspirational.
- **Offline-queue replay is non-idempotent** (posts with no idempotency key → double-book/double-charge on retry) and lossy (`_queue.shift()` silently drops oldest at 50). Add idempotency-key header + a regression test. (`shared/api/offlineQueue.ts:60,180`)

---

## 📈 Manager's Verdict — Overall Code Health

**Grade: B+ / strong pre-launch, with a short list of sharp edges before scale.**

Spinr's fundamentals are ahead of most Series-A ride-share stacks: disciplined Decimal money handling, atomic dispatch CAS, refresh-token reuse detection, RLS-by-default, PII scrubbing, a real circuit breaker, and a genuinely thoughtful error envelope. The audit culture (the tracked backlog, the migration-conflict runbook, the reconciliation cron) is better than the industry norm.

The risks that remain are **integration seams**, not foundational rot — and they cluster in the two places that cost real money and trust:

1. **Payments settlement has more than one entrypoint and they don't behave identically** (C1/C3/C6). Unify settlement into one function that credits the driver, issues the receipt, and reverses cleanly — everything else routes through it. This is the #1 pre-launch item.
2. **Dispatch has a live race and a large amount of dead/divergent matching code** (C2, DispatchService drift, dead PostGIS RPC, two timeout handlers). Delete the dead paths and close the accept-window race before traffic.
3. **Telemetry looks complete but is fragmented** — the stdlib/loguru split silently guts correlation on exactly the payment/auth/dispatch lines you'll need during an incident. One `InterceptHandler` fixes it.

Versus Uber/Lyft, the architectural distance is **durable workflows, a geospatial hot store, event streaming, distributed tracing, and feature flags** — none of which are needed for a Saskatchewan launch, but the first two (Redis GEO + a durable settlement workflow) are what I'd fund first, because the code is already 80% there (the PostGIS RPC exists; the settlement logic exists — both just need to be wired as the single canonical path).

### Recommended plan of attack (priority order)
1. **P0 — Unify settlement** (C1/C6): one crediting+receipt function, both entrypoints call it, Stripe calls threaded. + regression test.
2. **P0 — Close dispatch accept-race** (C2): re-assert `searching` atomically before re-dispatch.
3. **P0 — Refund/tip ledger reversal** (C3): compensating `financial_events` + GST proration.
4. **P1 — Surge dollar itemization** (C4) and **XFF/OTP throttle** (C5).
5. **P1 — `InterceptHandler`** for stdlib→loguru; flip the idle-timeout to fail-closed.
6. **P1 — Dispatch perf**: drop `columns="*"`, `gather` the per-driver fans, wire the existing PostGIS RPC.
7. **P2 — Delete dead code** (legacy timeout handler, DispatchService drift, second HTTP stack); enforce coverage floors; add SLO alerts.
8. **P3 — Strategic**: durable queue (Temporal/arq), OpenTelemetry, feature flags, Redis GEO / H3.
