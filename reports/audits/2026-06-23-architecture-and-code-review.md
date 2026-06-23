# Spinr — Comprehensive Code & Architecture Review

**Date:** 2026-06-23
**Scope:** Read-only teardown of backend (FastAPI/Supabase), rider/driver apps (RN/Expo), admin (Next.js), and shared TS. Benchmarked against rideshare market leaders (Uber/Lyft).
**Method:** Five parallel domain audits — security/auth, money/payments, dispatch/realtime, error-handling/telemetry, frontend/testing — cross-checked against `CLAUDE.md` non-negotiables.
**Nature:** Analysis only. No code was modified. File:line references are concrete and verifiable.

> **Headline:** This is a genuinely mature, security-conscious codebase — well above the median for a pre-launch rideshare platform. The error-sanitization pipeline, JWT trust model, Stripe idempotency, surge cap discipline, and SOS fail-safe are all done right and regression-tested. The findings below are a *hardening* list, not a rewrite. The cluster that actually matters: a handful of **PIPEDA PII-leak paths**, **two silent user-auto-create anti-patterns**, **money/receipt-transparency gaps**, and **operational-maturity gaps** (no staging, never-drilled failover, no executed load test) that are the real distance between Spinr and an Uber/Lyft-grade operation.

---

## 🚨 Critical Issues & Security Flaws

| # | Finding | Location | Why it matters |
|---|---------|----------|----------------|
| C1 | **Silent user auto-create on any valid JWT.** `get_current_user` creates a `role="rider"` row when the JWT is valid but the user row is missing — on *every* protected endpoint, not just OTP verification. A token for a deleted/phantom `user_id` re-mints the account and grants access. | `backend/dependencies/__init__.py:405–427` | Exactly the CLAUDE.md anti-pattern ("don't fall through to create-new-user"). Produces duplicate/zombie accounts and resurrects deleted (PIPEDA-erased) users. **Fix:** raise 401 "user not found", matching the `verify_otp` guard. |
| C2 | **Second auto-create path on WS connect.** Unknown Firebase uid on the WebSocket handshake creates a new rider. | `backend/routes/websocket.py:404–413` | Same class as C1; a driver token whose row hasn't propagated can mint a duplicate rider. **Fix:** reject unknown uid; never create on connect. |
| C3 | **Raw GPS in safety-team email body.** SOS/incident emails interpolate `latitude:.5f, longitude:.5f`. | `backend/features.py:1585` | PIPEDA prohibits raw lat/lng in transmissions that traverse SMTP intermediaries + `email_send_log`. **Fix:** geohash precision 5–6 or area label (still dispatch-grade). |
| C4 | **Full rider name in SOS SMS** to emergency contacts via Twilio (US infra + Twilio logs). | `backend/routes/rides.py:4715` | PIPEDA rule: full names → use `user_id`. **Fix:** first name only (sufficient for the emergency context). |
| C5 | **Raw GPS + free-text broadcast to all admin WS clients** in the `emergency_alert` event (`incident` dict carries raw lat/lng + reporter description). | `backend/routes/rides.py:4685` | Coordinates should never appear in a transmitted payload; admins can fetch the row from the DB. **Fix:** strip lat/lng + description from the WS payload. |
| C6 | **Plaintext admin email in `audit_logs.details`** (`target_email`) in unlock paths, while the Redis-path log lines correctly hash it. | `backend/routes/admin/auth.py:1383, 1395` | Audit rows are retained indefinitely and queryable by all audit-module staff. **Fix:** use the existing `_log_safe_email()` hash. |

**Done well (security):** OTP brute-force lockout (SHA-256 at rest, `hmac.compare_digest`, fail-closed 503 on Redis loss); refresh tokens are opaque 48-byte values stored hashed, rotated each use with a 60 s grace window + reuse-detection; audience pinning on `/auth/refresh` (rider≠driver≠admin); Stripe webhook signature verification (platform + Connect secrets) and `claim_stripe_event` idempotency before any side-effect; CORS wildcard blocked in prod; HSTS/CSP/XFO/COOP present; admin MFA (TOTP, per-account lockout, single-purpose enrollment token, backup-code hashing); break-glass is rate-limited + mandatory-audited; JWT trust model correctly re-reads rider/driver role from DB every request.

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

**User-facing leak surface: clean.** A sentinel-or-sanitize pipeline (`utils/error_handling.py`) scrubs every 5xx detail to `"Internal server error" + request_id` unless it carries an `ERR_*` key; a catch-all returns `"An unexpected error occurred" + request_id`; stack traces only ever hit logs. This is regression-tested (`test_error_response_sanitisation.py`). The 4xx `detail=` strings are intentional UX ("Insufficient wallet balance…"), not exception internals. **No raw stack traces or exception strings reach end-users.**

**Admin/telemetry gaps:**
- **MEDIUM — Sentry PII scrubber has coverage holes** (`backend/ai/pii.py:15–24`): misses SIN/government IDs, driver-license numbers, card PANs, full names, exact street addresses; the phone regex requires separators so a bare `3065551234` egresses. Defense-in-depth gap (primary defense is data-minimization + the pre-commit hook). **Fix:** extend the pattern set.
- **MEDIUM — `domain` tag missing on auto-captured events.** Only the loguru→Sentry path and the AI orchestrator attach `domain`; stdlib-logging errors and auto-captured 500s arrive with `surface=backend` only → incomplete triage-by-domain. **Fix:** derive `domain` in a generic `before_send`.
- **LOW — Metric/alert drift:** `spinr_payment_settlement_total` emits `outcome ∈ {success, failed, already_paid}` but CLAUDE.md documents `{success, failed, retry}`. **`retry` is never emitted** — any alert filtering `outcome="retry"` silently matches nothing. **Fix:** reconcile the doc (or emit `retry` from the retry loop).
- **LOW — Logout JTI-blacklist / WS-kick failures swallowed** (`admin/auth.py:541–548` bare `pass`; `:619` logs at `warning`). Redis outage during logout/logout-all leaves sessions live up to the 1 h TTL with no signal. **Fix:** `logger.error`.

**Done well:** the sanitize-or-sentinel contract, DB/auth/payment errors consistently re-raising (`details['original']` + `exc_info`) instead of warn-and-continue, all 7 required Prometheus metric names emitted under the exact spec spelling, `send_default_pii=False`, and an unmissable ERROR when prod boots without `SENTRY_DSN`.

---

## 🐢 Performance Bottlenecks & Optimizations

| # | Finding | Location | Impact |
|---|---------|----------|--------|
| P1 | **N+1 quest-progress query in the dispatch notify loop** — up to `max_offers` (≤10) serial Supabase round-trips *before* the offer push goes out. | `backend/routes/rides.py:919–943` | Directly on the offer→notification path (SLA <2 s). The rider/polygon enrichment was parallelized; quest lookups were left serial. **Fix:** batch with `.in_(driver_uids)` or `asyncio.gather`. |
| P2 | **Per-point `redis_incr` in the WS location batch** — a 500-point batch = 500 serial Redis round-trips on the receive loop, blocking all other messages for that socket. | `backend/routes/websocket.py:792–793` | The wrapper already has `redis_incrby` (one RTT). **Fix:** `redis_incrby(key, len(points))`. |
| P3 | **WS pub/sub outbox writes 2 Redis RTTs (`INCR` + 3-op pipeline) on *every* unicast**, including 1 Hz `driver_location_update` fan-out. | `backend/utils/ws_pubsub.py:175–186` | Multiplies Redis load and `spinr_ws_fanout_duration_ms` (SLA <100 ms). Location updates are ephemeral and need no replay buffer. **Fix:** skip the outbox for high-frequency ephemeral message types. |
| P4 | **Heartbeat re-reads `token_version` from DB every 10 s per socket** — N online drivers = N DB reads / 10 s purely for revocation. | `backend/routes/websocket.py:280–298` | Uncached DB load competing with dispatch reads at fleet scale. **Fix:** cache with short TTL, or push revocation via the existing `kick_user` pub/sub channel instead of polling. |
| P5 | **Surge engine: Python point-in-polygon capped at 500 drivers.** | `backend/utils/surge_engine.py` (backlog D1) | Won't scale; should move server-side (PostGIS/H3). |

**Done well:** dispatch pushes already moved off the request path; estimate polyline fetch overlapped with fare work; `redis_mget` batching of offer-skip keys; partial composite dispatch index (`idx_drivers_dispatch_ready`) turns the candidate query into an index-only lookup; per-message broadcast timeout prevents one stuck socket stalling fan-out.

---

## 💡 Tech Stack & Architecture Recommendations

**Current stack is modern and well-chosen:** FastAPI 0.136 + Python 3.12, Supabase (Postgres + RLS) with a service-role wrapper, Redis pub/sub for cross-replica WS fan-out, Stripe, Firebase/FCM, Twilio, Sentry + Prometheus, RN/Expo 55, Next 16/React 19, zustand, maplibre-gl. Nothing here needs replacing.

**Where Spinr diverges from Uber/Lyft — and what to do about it:**

| Area | Spinr today | Uber/Lyft pattern | Recommendation |
|------|-------------|-------------------|----------------|
| **Dispatch/matching** | In-process `asyncio` timers for offer-timeout + batch-claim that holds drivers `is_available=False` for ~15 s; timers are **not replica-aware** (lost on pod restart → 60 s stuck-ride recovery). | Dedicated stateful matching service (Uber DISCO), consistent hashing (Ringpop), durable timers/workflows (Cadence/Temporal). | Don't build microservices yet — but move offer-timeout to a **durable timer** (DB-backed claim + sweeper, or Temporal) so it survives restarts, and shrink the batch-claim hold (see R1/E2 below). This is the single biggest architectural fragility. |
| **Geospatial** | Python haversine / point-in-polygon, 500-driver cap. | H3 hex indexing, PostGIS. | Adopt **PostGIS** (already in Supabase) or **H3** for candidate selection and surge counts. |
| **Realtime fan-out** | WS + Redis pub/sub, per-message outbox. | Purpose-built edge realtime. | Fine for stage; just stop buffering ephemeral location pings (P3). |
| **Event backbone** | Direct DB writes + background loops. | Kafka event streaming. | Not needed at this scale; revisit only if analytics/audit volume forces it. |
| **Resilience** | DNS failover Railway↔Fly (**never drilled**), no staging env, no executed load test, no external synthetic monitor. | Region failover + chaos engineering + continuous load. | **Highest-leverage gap.** Stand up staging (E1), run the existing `loadtest/locustfile.py` (E2), add an external `/health` synthetic probe → PagerDuty (E4), and actually drill the failover (C1). |
| **Tracing** | `X-Request-ID` propagation only. | Distributed tracing (Jaeger). | OpenTelemetry is backlogged (D2); fine to defer until multi-replica latency debugging hurts. |
| **Surge** | Hard 2.5× cap, never retroactive, visible pre-booking. | Unbounded dynamic pricing. | **This is a deliberate differentiator, not a gap — keep it.** |
| **Commission** | 0% driver commission. | 20–30% take rate. | Core brand promise; verified intact (`platform_share = 0`). |

---

## 🛠️ Maintainability & Code Smells

- **MEDIUM — Dead/divergent `cachedClient.ts`** reads `auth_token` from `localStorage`/SecureStore — a key the memory-only auth model no longer writes, so it would 401 on every request if wired up. Not imported anywhere. **Fix:** delete or delegate to `client.ts`'s `getAuthHeader`. (`shared/api/cachedClient.ts:40–61`)
- **MEDIUM — `DEFAULT_FARE` uses float literals** (`3.50`, `1.50`…). Safe today only because every read goes through `str()`→`_d()`; one contributor doing `DEFAULT_FARE["base_fare"] * surge` introduces silent float drift. **Fix:** make them string literals. (`backend/services/fare_service.py:31–37`)
- **MEDIUM — Corporate allowance spend may call `apply_rollback`** (a *reversal* op) to debit at settlement — accounting could be inverted. **Verify the RPC semantics and rename/document.** (`backend/services/payment_service.py:280–287`)
- **LOW — Offline queue replays non-idempotent POSTs** with no idempotency key (a queued `POST /rides` can duplicate). Backend has a duplicate-ride guard, so impact is mitigated. **Fix:** client-generated `Idempotency-Key` per queued mutation. (`shared/api/offlineQueue.ts`)
- **LOW — SOS nested retry multiplies attempts to ~9** (`SOSButton` 3× × `triggerEmergency` 3×), slowing the failure dialog. **Fix:** one layer owns the retry. (`SOSButton.tsx:129` × `rideStore.ts:837`)
- **LOW — Residual `any`** in `CustomAlert`, `CarMarker`, `errorReporting`, `ErrorBoundary` (none on money/auth paths).
- **LOW — Transient-error detection by substring/type-name matching** of httpx/h2 internals (`repositories/_base.py:208–211`) — a dependency rename silently flips retryable→hard-fail (safe direction, but brittle; pin with a test).

---

## 🧪 Testing & QA (Missing Edge Cases)

**Strong baseline:** ~250 backend test files. Ride state machine (14 tests + full `searching→completed` E2E), Stripe webhooks (broad event-type + idempotency), auth/RLS/CSRF/cookie/token-revocation, surge (29 tests incl. 2.5× cap + corporate bypass), and frontend (refresh single-flight, SOS-exemption, HttpOnly-cookie E2E, WAV gating) are all covered. E2E suite covers cancellation, payment-guard (no double-charge), driver-cancel, SOS, rating regression.

**Gaps:**
- **HIGH (process) — Per-module coverage floors not enforced.** Global floor in `pytest.ini` is 60%, but CLAUDE.md mandates ≥90% for `payments.py`/`fare_service.py` and ≥80% for `rides.py`/`dispatch_service.py`. This is ACTION_ITEMS **A1**, flagged as "the single biggest remaining gap." **Fix:** `coverage report --fail-under` per path in CI; ratchet.
- **MEDIUM — Fare-calc branch coverage thin** (`test_fares.py` = 4 tests). `calculate_fare`'s minimum-fare floor, booking fee, and distance×time-under-surge branches aren't directly unit-tested; the ≥90% gate may be passing on indirect coverage. **Fix:** add direct branch tests.
- **LOW — GST/PST rate *computation* not unit-tested at source** (only the *rendering* as separate line items is). Add a test pinning the 5%/6% math for a SK fare.
- **LOW — Promo × surge × corporate interaction not co-tested** — discount-vs-surge-vs-tax ordering is exactly where fare disputes originate.

---

## 💸 Money & Receipt Integrity (spans Critical + QA)

Pulled out because it crosses the no-hidden-fee brand promise and CRA retention:
- **BLOCKER — Receipt bundles base+distance+time into one "Ride fare" line** (`fare_service.py:242–249`, mirrored `routes/rides.py:492–495`); the surge line item carries `amount: None`. Violates the "every charge maps to a disclosed line item" non-negotiable — a rider can't verify the per-km math or the surge delta. **Fix:** emit three separate component lines + a computed surge amount.
- **BLOCKER — Surge formula deviation:** `calculate_fare` surges only distance+time, not `base_fare`; the documented formula surges the whole subtotal. At 2.5× on $3.50 base + $10 distance the code charges $28.50 vs the spec's $33.75. **Either undercharging or the doc is wrong — needs product sign-off**, then align code or comment the deviation. (`fare_service.py:204–207`)
- **BLOCKER — Wallet top-up idempotency key is time-bucketed** (`f"wallet-topup-{uid}-{minute}"`) — a retry 61 s after a network drop creates a second PaymentIntent → double-charge. **Fix:** client-supplied per-request UUID. (`routes/wallet.py:189`)
- **BLOCKER — Float in the tax path:** `calculate_all_fees` returns `float(...)` tax amounts; `/fare-estimate` uses built-in `round()` on floats; tax stored as float in `tax_breakdown` JSONB for the **7-year CRA-retained** value. **Fix:** keep `Decimal`/strings to the serialization boundary. (`features.py:866, 877, 928`)
- **BLOCKER — Corporate payment-source priority not implemented:** `settle_corporate` skips the rider-wallet-first step (wallet→allowance→master→card). **Fix:** debit rider wallet first per policy. (`payment_service.py:265–285`)
- **WARNING — Minimum-fare uplift not surfaced as a line item** — when `max(subtotal, minimum)` fires, components don't sum to the total. (`fare_service.py:207`)
- **WARNING — Refunds write no `financial_events` ledger row** (credits do) — incomplete audit trail. (`webhooks.py:701–714`)

**Verified clean:** Stripe idempotency gate first in every webhook branch; integer cents at the Stripe boundary; `currency="cad"` always explicit; surge capped at 2.5× in two independent places; surge locked at estimate (never retroactive); corporate rides force surge=1.0; `platform_share = 0` invariant intact; receipt sent only after settlement.

---

## ⚖️ Regulatory / Insurance (Saskatchewan + PIPEDA)

- **HIGH — False Period-1 insurance audit rows.** The batch offer-timeout handler and the accept-path loser-release call `record_period_transition(driver, 1)` **without** the `if released.get("is_available")` guard the single-offer handler already has — so an offline driver gets a spurious TNC-contingent-liability audit row. Period rows are append-only and regulator-audited. **The existing single-handler fix was not mirrored.** (`rides.py:1297–1299`, `drivers.py:3564–3565`)
- See C3–C5 for the PIPEDA GPS/name leaks.

---

## 🧨 Resilience / Availability (HIGH)

- **`redis_set_nx` fails *open*** — on a Redis error it falls through to the in-process dict and returns `True` (lock "acquired") on *every* replica. The daily leader-lock loops (retention purge, Stripe reconcile) then run concurrently on all replicas → duplicate purge/reconcile passes. Other functions in the file `raise`; this one should too (fail closed). (`redis_client.py:153–163`)
- **Untimed blocking calls on hot paths:** Twilio `messages.create()` is synchronous + untimed, `await`ed inline in the login handler (`sms_service.py:37` ← `auth.py:315`); async Redis client sets **no socket timeout** (`redis_client.py:88`), so a *hung* (not refused) Redis blocks the dispatch/OTP path until OS TCP timeout. The sync rate-limiter already sets timeouts — mirror it. **Fix:** `asyncio.to_thread` + explicit timeouts.
- **Mass auto-offline during a Redis outage:** when the presence filter degrades (fails open to all DB-online drivers), offers go to drivers whose sockets are down, then the timeout handler increments miss-streaks and can `auto_offline` the whole fleet. **Fix:** suppress miss-streak penalties when presence is degraded. (`rides.py:734–743`)
- **Dispatch batch-claim "hostage window" (R1) + non-replica-safe duplicate dispatch (E2):** a ride holds up to N drivers `is_available=False` for ~15 s, invisible to every other concurrent ride; overlapping retry/timeout timers can double-dispatch with no unique constraint on `(ride_id, driver_id, status='pending')`. Erodes match rate (KPI ≥85%) under contended supply. **Fix:** claim-on-accept (or release losers on first accept) + a durable, replica-safe timer.

---

## 📈 Manager's Verdict

**Overall code health: B+ / strong pre-launch.** This codebase is materially better engineered than most rideshare platforms at the same stage. The team has internalized hard-won lessons (the migration-conflict incident, the duplicate-account anti-pattern, the SOS fail-safe) and the conventions in `CLAUDE.md` are *actually followed* in the hot paths — that's rare. Security fundamentals (auth, Stripe idempotency, error sanitization, surge cap, RLS) are sound and tested.

**What's holding it back from an Uber/Lyft-grade operation is not the code — it's two things:**

1. **A short tail of correctness/compliance bugs** that slipped the conventions: two silent user-auto-create paths (C1/C2), six PIPEDA PII-leak sites (C3–C6, plus Zoho free-text), the false-Period-1 insurance rows, and the money/receipt-transparency cluster. These are individually small and individually fixable in a focused sprint — but they touch the two areas (PII + money) where a single miss is a regulatory or trust event, so they should gate launch.

2. **Operational maturity.** The architecture (monolith + Supabase + Redis) is the *right* choice for this stage — resist the urge to microservice it. But the platform has never been load-tested against its own SLAs, the failover has never been drilled, there's no staging environment, and nothing outside the system probes it. Uber/Lyft's edge isn't exotic tech; it's that they *know* their breaking points and rehearse failure. That gap is closable with the existing backlog (E1/E2/E4 + C1) and no new code.

**Maintainability:** high — clear domain separation, strong typing trend, dead code is rare and small. **Readability:** high. **Scalability:** good to mid-six-figure rides/day on the current design; the dispatch in-process timer and Python geospatial are the first two ceilings to raise (durable timers, then PostGIS/H3).

**Recommended sequencing (the plan):**

- **Gate launch (P0, ~1 sprint):** C1–C6 (auth auto-create + PIPEDA leaks), the money blockers (receipt line items, surge formula sign-off, wallet idempotency key, float-in-tax, corporate priority), and the false-Period-1 fix. Each is ≤3 files, each needs a regression test per CLAUDE.md.
- **Harden before scale (P1):** `redis_set_nx` fail-closed, Twilio/Redis timeouts, dispatch hostage-window + replica-safe timer, mass-auto-offline guard, Sentry scrubber + `domain`-tag coverage, per-module coverage floors (A1).
- **Operational maturity (P1, mostly no code):** staging (E1) → execute load test (E2) → failover drill (C1) → synthetic monitoring (E4). This is the highest-leverage work for de-risking a public launch.
- **Post-launch (P2/P3):** N+1/Redis-RTT perf cleanups (P1–P4), PostGIS surge query (D1), durable dispatch timers, kill switches (E5), forced-upgrade gate (E3).

*No code was changed in producing this review.*
