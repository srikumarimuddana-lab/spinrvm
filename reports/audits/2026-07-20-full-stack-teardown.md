# Spinr — Full-Stack Engineering Teardown & Remediation Plan

**Date:** 2026-07-20
**Scope:** Read-only review of all five surfaces (`backend/`, `rider-app/`, `driver-app/`, `admin-dashboard/`, `shared/`) plus CI/CD and test posture.
**Method:** Six parallel specialist passes — security, money/payments, backend performance/architecture, error-handling/telemetry, mobile, and admin+testing/CI. Every finding below was verified against source; speculative items were dropped.
**Nature:** This is an assessment and a plan. No code was modified.

---

## Executive framing

Spinr is a genuinely well-built platform for its stage. The sprint log shows a disciplined P0 burn-down, and the audits confirm it: the JWT trust model, Stripe idempotency, corporate-wallet locking, RLS-first migrations, PII log hygiene, surge cap, and the core fare math are all **correct and defended by tests**. There are **no launch-blocking security or money-loss defects** in this pass.

What the review surfaces is a different, expected class of risk for a platform at this maturity: (1) a handful of real **correctness bugs** that quietly break a payment method, under-refund riders, or disable a safety timer; (2) a set of **performance/architecture ceilings** that are invisible at Saskatchewan launch volume but will breach the P95 SLA table under load; and (3) **operational-readiness gaps** (staging, forced-upgrade gate, kill switches, coverage enforcement) that are cheap now and expensive to retrofit. None individually is a crisis; together they are the difference between "works in a demo" and "survives a real Friday-night surge."

The comparison to Uber/Lyft is in the Tech Stack section — the short version: Spinr's architecture is *correctly sized* for its stage and its deliberate divergences (0% commission, capped surge, transparency) are strengths to protect, not gaps to close. The one place it has genuinely out-grown its tooling is the data-access layer (PostgREST-over-HTTP through a thread pool).

---

## 🚨 Critical Issues & Security Flaws

No P0 blockers. The highest-severity items are one admin XSS vector and a cluster of correctness bugs with real user/money impact.

| # | Severity | Location | Defect | Impact |
|---|---|---|---|---|
| C1 | **High** | `admin-dashboard/.../support-tickets/tickets/[id]/page.tsx:90-96` | `toText()` "strips" untrusted customer-email HTML by assigning it to a detached `div.innerHTML`, then reads `textContent`. Detached-element `innerHTML` still fires `<img src=x onerror=…>` / `<svg onload=…>` handlers. The comment claims it prevents XSS; it does the opposite. | Arbitrary JS in the admin origin from any customer who emails a support ticket → can read the in-memory admin token and act as admin. CSP nonce does **not** save you here (inline event handlers on parsed nodes fire regardless). **Fix:** `new DOMParser().parseFromString(html,"text/html").body.textContent` or DOMPurify. |
| C2 | **High** | `shared/api/client.ts:762-806` | 401 → refresh → `retryFn()` re-enters with a *fresh* retryFn and no "already retried" marker. If refresh keeps succeeding while the endpoint keeps 401ing (token-version race, revoked role, clock skew), it loops refresh→retry→401 with no cap or delay. | Battery/network drain on device, backend hammered by every affected client, hung spinner. **Fix:** thread a `retriedOnce` flag; after one retry, fall through to session-clear. |
| C3 | **High** | `shared/api/client.ts:794-806` → `authStore.ts:606` | A *transient* refresh failure (network/5xx) during a 401 burst falls into the sign-out branch and calls `logout()`, which **deletes the refresh token** — defeating `refreshTokens()`'s deliberate "keep session on transient error" design. | Access token expiry + one moment of bad connectivity = full sign-out to OTP. For a driver mid-trip it also fires the go-offline PUT. **Fix:** distinguish "refresh rejected" (sign out) from "refresh transiently failed" (clear memory token only, keep refresh token). |
| C4 | **High** | `rider-app/store/rideStore.ts:543-625` | Offline queue replays `create_ride` with **no idempotency key** (the live path has one) and a stale fare quote, up to 3× across reconnects. Nothing writes this queue anymore — it's a reader-only vestige, so anything it finds is an old-app-version booking it will replay unsafely. | Duplicate/ghost bookings and stale-surge bookings on reconnect. **Fix:** delete the dead queue reader, or attach `request.id` as a stable idempotency key + a max-age cutoff. |
| C5 | **High (correctness/money)** | `routes/wallet.py:251-256` | `/wallet/pay` guards `req.amount` against `total_fare` (pre-tax subtotal) instead of `grand_total` (what the rider actually owes). On any taxed ride `grand_total > total_fare`, so a correct payment is rejected `ERR_FARE_EXCEEDED`; sending `total_fare` instead trips the RPC's `ERR_FARE_UNDERPAID`. | **Wallet payment is functionally broken for essentially every taxed ride.** Not a money-loss (the RPC prevents undercharge) but it silently disables a whole payment method. **Fix:** use `grand_total` with `total_fare` fallback, mirroring `_authoritative_ride_charge`. |
| C6 | **High (correctness/money)** | `routes/disputes.py:86-87, 208-213` | `original_fare` and the default "full refund" amount derive from `total_fare`, not `grand_total`. A rider who paid $22 ($20 + $2 tax) gets a dispute record advertising `original_fare=$20`, and `admin_resolve_dispute` hard-caps refunds at that figure. | Admin **cannot** refund the actual amount charged; if they trust the suggested amount, the rider is silently **under-refunded by the tax/fee delta** on every dispute. **Fix:** derive from `COALESCE(grand_total, total_fare)`. |
| C7 | **High (safety correctness)** | `utils/safety_checkin_loop.py:80-101` | The loop reads `ride_started_at` — a column it never SELECTs (projection is `id,rider_id,started_at,updated_at`) — so it always falls back to `updated_at`, which every ride UPDATE bumps. The 20-minute safety timer is perpetually reset on active trips. | The long-trip safety check-in **may never fire**. Same-day fix, independent of the perf angle (it also scans all in-progress rides with the age filter in Python + does 3 sequential Redis GETs/ride). **Fix:** correct the column, push the cutoff into the query, MGET the Redis reads. |

**Security hardening (verified, lower severity):** admin IP allowlist uses `startsWith` (so `1.2.3.4` admits `1.2.3.40-49`) and trusts the client-controlled leftmost `x-forwarded-for` — spoofable when the allowlist is the only gate (`admin-dashboard/src/middleware.ts:94-99`); admin middleware never verifies the JWT signature (decode + `exp` only) and `/api/auth/set-cookie` accepts any string as `admin_token` while logging it on failure (`jose` HS256 verify works in Edge now — the "can't access secret at edge" rationale is stale); `AdminDebitRequest.amount` is typed `float` while its sibling credit request is `Decimal`; `admin_update_driver` takes a raw `Dict[str,Any]` (safe today only via a manual allowlist); and `POST /admin/auth/unlock` + `POST /drivers/{id}/reveal-sin` (regulated data) carry no rate-limit decorator.

**Confirmed clean (no action):** JWT role re-read for non-admin tokens, IDOR ownership checks on ride/receipt/share/chat, Stripe + Twilio + SNS webhook signature verification (no SSRF via forged SubscribeURL), `maps_proxy` bounded to hardcoded Google endpoints, Supabase parameterized filters (no injection), RLS-first on recent migrations, PII log hygiene (phone last-4, hashed email), and the SOS flow (allow-expired token, never auto-dials, never leaks contact numbers).

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

The scrubbing infrastructure is strong (global sanitized 500s with `X-Request-ID`, Sentry `send_default_pii=False`, request-id correlation). The problems are over-scrubbing on one side and under-surfacing on the other.

- **E1 (P1) — The 5xx sanitizer eats vetted user-facing copy.** `utils/error_handling.py:44-60,737-748` only lets `^ERR_[A-Z0-9_]+$` sentinels through; everything else 5xx becomes "Internal server error." Dozens of curated messages are silently replaced, including on the highest-anxiety path: "Payment provider error. Please try again." and "Wallet payment failed — please retry" both become "Internal server error." Worse, each scrub also emits an extra ERROR line that the loguru→Sentry bridge forwards, **doubling Sentry noise per payment blip.** Fix: promote curated messages to `ERR_*` sentinels (with client copy mapping) or a `PublicHTTPException` marker so vetted text passes while `str(e)` is still scrubbed.
- **E2 (P1) — Admin dispute endpoints swallow DB errors into empty data.** `routes/admin/support.py:123-125,158-166` catch any exception, `logger.warning("table may not exist")`, and return `[]` / all-zero stats. During a DB incident the admin sees a clean, empty dispute queue and refund/chargeback work silently stalls — a direct violation of the "never warning-and-continue on DB errors" rule (line 148 in the same file does it right with a 503). Fix: `logger.error(exc_info=True)` + `HTTPException(503,"ERR_DATABASE")`.
- **E3 (P1) — Permanently-failing background loops never alert.** `allowance_reset.py`, `corporate_autotopup.py`, `payment_retry.py`, `driver_claim_reaper.py` record their heartbeat *even when the tick throws*, so the watchdog (which only fires on staleness) never trips. A loop that fails 100% of the time logs forever and pages no one. Compounding: only 8 of 16 loops emit `spinr_bgloop_errors_total`, and **payment-retry, scheduled-dispatch, surge, and stripe-reconcile are among the ones that don't** — the money/dispatch loops are exactly the blind spots. Fix: heartbeat only on success (or track an error streak), emit the counter from every loop, alert on N consecutive failures.
- **E4 (P2) — Unhandled-500s omit the `detail` field the mobile client reads.** `error_handling.py:867-881` returns only a nested `error` object; the other handlers deliberately include top-level `detail` (documented at :712-715). On a true crash the user gets a generic "Request failed" and the `request_id` lives only where the client never looks — so support can't get a correlation id for the errors that most need one. Fix: add top-level `detail` with the ref id.
- **E5 (P2) — Raw GPS in a log line.** `routes/rides/matching.py:44-47` logs `pickup=({lat},{lng})` — PIPEDA says geohash at most. The function is a deprecated no-op but the log fires precisely when a stale caller hits it. Fix: drop coordinates (vehicle_type_id + geohash suffices).
- **E6 (P2/P3) — Zoho + LMS + CSV-import error paths** either get scrubbed into uselessness (admin configuring an integration sees "Internal server error") or, on their 4xx branches, pass raw upstream/`str(e)` text through (`support_tickets.py:53`, `admin/drivers.py:1868`, `admin/driver_import.py:67,90`). Fix: fixed sentinels, keep raw text server-side only.
- **E7 (P3) — Most Sentry error events lack the mandated `domain` tag.** The bridge only promotes tags from loguru `extra={}`, which most `logger.error` calls omit — so triage-by-domain silently doesn't work for the noisiest domains (payments, dispatch, scheduled). `corporate_autotopup.py:163` also logs without `exc_info`, losing the `DatabaseError` original. Fix: add `extra={"domain":…, "ride_id":…}` on error logs, or derive a default domain from module path.

**Verified good:** all seven documented KPI metrics are emitted with correct names and regression tests; `/metrics` is bearer-auth'd with a production warning; the global handler chain returns sanitized JSON with correlation IDs.

---

## 🐢 Performance Bottlenecks & Optimizations

The single systemic issue is the data-access layer; everything else is a specific SLA-path consequence of it. (Correction to internal docs: the DB pool defaults to **64** threads, retry is tiered read=3/write=1.)

- **P1 (systemic ceiling) — Every DB op is a blocking PostgREST HTTPS round-trip through one 64-thread pool.** `repositories/_base.py:138-236`. Request latency = Σ(serial round-trips); at ~30-50ms/RT the pool caps a replica at ~1.3-2k ops/s shared across all requests + 16 loops + WS revalidation. Queued work is **not** cancelled when the caller's deadline lapses (`future.cancel()` only works pre-start), so a burst leaves the pool draining dead work while new requests 503. This is the platform's real scaling wall. **Fix:** asyncpg (or Supabase's pgbouncer port) for the hot paths (dispatch candidate query, ride state transitions, location write, offer claim); keep PostgREST for the long tail; propagate deadline into queued work.
- **P2 (dispatch SLA <2s) — A dispatch attempt stacks 15-25 serial round-trips.** `routes/rides/matching.py` + `services/dispatch_service.py`: the `service_areas` row is re-fetched 4×, the driver-claim loop does 2-3 RTs *per driver* serially, and there's an N+1 `quest_progress` query per claimed driver. That's 0.7-1.5s before the phone rings — most of the budget. **Fix:** 60s TTL cache for `service_areas`; batch quests via one `.in_()`; `asyncio.gather` the (already-atomic) claims; long-term collapse claim+re-read into the existing-but-unused `match_and_claim_driver` RPC.
- **P3 (WS fan-out <100ms) — The pub/sub consumer is a single serial task with a 2s per-send timeout.** `utils/ws_pubsub.py:324-417`. Every unicast (offers, `ride_taken`, status, 1Hz location) flows through one loop; one half-open socket head-of-line-blocks everything behind it for up to 2s. **Fix:** dispatch each delivery as a bounded-semaphore task; cut the stuck-socket timeout to ~250ms with immediate reap.
- **P4 (settlement <1s) — Ride completion runs ~25+ sequential awaits** (`routes/drivers/ride_complete.py:141-800`) including a retry loop with `asyncio.sleep`, before the receipt renders. **Fix:** `gather` the independent reads (rider ∥ incentives ∥ snapshot), fold status-flip+earnings+availability into one RPC, defer non-receipt work.
- **P5 (stall mode) — Redis clients have no socket timeouts or pool limits.** `utils/redis_client.py:78-94`, `ws_pubsub.py:131`. The careful fail-open logic only triggers on *exceptions*; a black-holed TCP connection blocks `MGET`/`GET` awaits **forever** with no `wait_for`, stranding dispatch and every cached-row read. **Fix:** `socket_timeout=1.0, socket_connect_timeout=1.0, health_check_interval=30`, plus `wait_for` on the dispatch-path lookups.
- **P6 (cheap wins)** — full-history "aggregate in Python" endpoints pull up to 25k `select('*')` rows (`drivers/earnings.py`, `rides/queries.py:254` — and the silent 10k cap makes `payable_balance` wrong past that, which bounds Stripe payouts); the fare estimate fetches every active service area *with polygon JSON* on every call (`estimates.py:152`); and the 3-second location UPDATE returns the full driver row (encrypted PII) via PostgREST's default `return=representation`. **Fixes:** Postgres aggregate RPCs / earnings ledger; 30-60s TTL cache of service areas; `returning="minimal"` on location writes (the cheapest single win for the <150ms write SLA).
- **P7 (loop hygiene / correctness)** — the WS `location_batch` handler does up to 500 serial `redis_incr` calls in the receive loop despite `redis_incrby` existing (`websocket.py:915`); Redis leader locks silently degrade to per-replica in-process locks on Redis failure, so daily finance/reconciliation jobs run once per replica during an outage (`redis_client.py:145-163`); `update_acceptance_rate` is a non-atomic read-modify-write run in 200-wide gather storms on batch offer-expiry (`driver_repo.py:274`); and admin Stripe calls run through the *DB* thread pool, so a Stripe brownout (30s×2 retries) shrinks DB capacity for dispatch (`admin/rides.py:1299…`).

---

## 💡 Tech Stack & Architecture Recommendations

*(See the companion table below for the full Uber/Lyft comparison.)*

**Where Spinr is correctly sized and should NOT chase the leaders:** the modular monolith (Uber runs ~4,500 microservices and Lyft has been consolidating back — premature decomposition would just multiply the failover surface), single-PSP Stripe, rule-table capped surge (a deliberate regulatory/trust stance, not a gap), and Google Maps for rider-facing quotes. No Kafka, no service mesh, no active-active until ~10k concurrent rides.

**Where it has genuinely outgrown its tooling — ranked:**

1. **asyncpg for hot paths** (the P1 ceiling). Direct Postgres wire protocol + true async + prepared statements removes the PostgREST HTTP tax and the thread-pool wall. Supabase exposes the port; front it with PgBouncer transaction pooling. Highest-leverage single change on this list.
2. **PostGIS now, H3 later** (`ST_DWithin` on a GiST-indexed `geography` column) for nearby-driver and surge counting — already tracked as D1; raise its priority because it compounds with every location feature. Uber's H3 / Lyft's S2 are the eventual endpoint; PostGIS is the right-sized step.
3. **Redis Streams for the 16 background-loop work queues** — converts polling into consumer-group event processing with built-in replay safety, cutting idle DB polling to zero and improving reaction latency from interval-average to near-instant.
4. **OpenTelemetry tracing** (FastAPI + asyncpg + Redis + httpx, 10% sampling) — you cannot debug a 2s dispatch SLA breach across 5 hops with request IDs alone. Cheap now, expensive to retrofit.
5. **Feature-flag / kill-switch layer** (E5 in ACTION_ITEMS) — even an `app_settings`-backed decorator at the top of each risky loop/path. Uber's golden rule: everything that spends money or pages a human has a kill switch.
6. **Forced-upgrade gate (E3) + staging (E1) + external synthetic monitoring (E4)** — the three cheapest items with the highest launch-risk reduction; all already in the backlog, none done.
7. **GPS plausibility service** (speed/teleport detection on the existing breadcrumb pipeline) — protects insurance-period integrity and future incentive spend; promo fraud finds small platforms fast.

### Uber / Lyft ecosystem comparison

| Capability | Uber / Lyft | Spinr today | Gap |
|---|---|---|---|
| Geospatial | H3 (Uber) / S2 (Lyft) cell indexing | Python Haversine + point-in-polygon over fetched rows | HIGH at scale, LOW at launch → PostGIS |
| Dispatch | Batched global matching, ETA-based | Greedy nearest-driver, sequential 15s offers | MEDIUM — fine at low density; keep matching in one module so batching is retrofittable |
| Event backbone | Kafka | DB writes + Redis pub/sub + polling loops | MEDIUM → LISTEN/NOTIFY or Redis Streams; Kafka is overkill here |
| DB access | Direct binary protocol, aggressive pooling | PostgREST-over-HTTP in a thread pool | **HIGH — the real ceiling → asyncpg** |
| Observability | Jaeger + M3, full-fidelity tracing | Sentry + Prometheus + request-IDs, no tracing | MEDIUM → OpenTelemetry |
| Release safety | Staged rollouts, flags, forced-upgrade, shadow traffic | EAS + OTA, none of the above | HIGH for launch → E1/E3/E5 |
| Fraud/risk | Dedicated ML risk platforms | App Check + attestation + OTP lockout | LOW now, MEDIUM post-launch → GPS plausibility |
| Payments | In-house double-entry ledgers, multi-PSP | Stripe + wallet tables + daily reconcile cron | LOW — correctly sized; formalize a ledger before more money products |

---

## 🛠️ Maintainability & Code Smells

- **Dead-but-dangerous code.** `shared/api/cachedClient.ts` is broken-by-design (reads a SecureStore token key that `authStore` deletes, so every request is unauthenticated; also lacks timeout/refresh/App-Check/CSRF) — zero importers, a landmine. `rideStore` offline-queue reader (C4) is a vestige nothing writes. `matching.py:create_demo_drivers` is a deprecated no-op that still logs raw GPS. **Delete all three.**
- **Monolithic components.** `admin-dashboard/.../drivers/page.tsx` is 2,865 lines and layers a client-side search filter over server pagination — so search only matches rows on the currently loaded page (silent missing results); the users and rides pages already do this correctly server-side.
- **Documentation drift.** CLAUDE.md still says "Expo SDK 54" (apps are on ~55.0.26 / RN 0.85.2) and "next migration slot 145" (actual tail is 243); `ci.yml` comments cite "Railway primary" while CLAUDE.md now says Fly primary. The deploy pipeline itself references a **Render** fallback that appears nowhere in the documented topology — three deploy targets, real config-drift risk (the post-deploy smoke test may validate the standby).
- **State-reset hygiene.** `driverStore` never registers a logout callback, so banking/earnings/PII survive into the next login on a shared device (rideStore does it right); rider PII (recent searches, saved addresses, persisted raw lat/lng) similarly survives logout.
- **Consistency nits.** float-vs-Decimal between sibling admin money requests; raw-dict vs Pydantic-model between sibling admin endpoints; two web-storage backends (`sessionStorage` read vs `localStorage` clear) in the same 401 path.

---

## 🧪 Testing & QA (Missing Edge Cases)

- **T1 (High) — The money-path coverage mandate is unenforced.** `pytest.ini` has a single global `--cov-fail-under=60`; there are **no per-file floors** despite CLAUDE.md's ≥90% for payments/fare/crypto and ≥80% for rides/dispatch. Worse, `.coveragerc` omits modules that *are* production runtime (`sms_service.py` — the OTP path; `geo_utils.py` — fare+dispatch distance math) under a "diagnostic scripts" label, so the reported 60% is inflated and untested dispatch geometry is invisible. Fix: `coverage report --fail-under=90 --include=…` per path; un-omit imported modules.
- **T2 (High) — No true E2E in CI.** Admin Playwright specs are `continue-on-error` on PRs (blanket flaky-suppression that defeats the gate) and fully mock `**/api/**` — they test UI wiring against fixtures, never a backend contract. Backend lint (`ruff`) also runs `continue-on-error`. Fix: make E2E blocking with real retries + a quarantine tag; add a nightly run against staging.
- **T3 (Med) — Lifecycle E2E gaps.** No full booking→settlement flow for **scheduled rides through the dispatch loop, surge-priced bookings (quote→charge parity), or refund/dispute end-to-end** (unit coverage exists for each; the integrated path doesn't). The canonical `test_ride_state_machine.py` is also missing `scheduled→searching`, offer-timeout revert, and auto-cancel transitions (covered in scattered files — consolidate per the CLAUDE.md rule).
- **T4 (Med) — Mobile screen coverage is near-zero.** Rider-app has 41 route files, 16 tests, all lib/store-level — `collectCoverageFrom` includes only `store/**`, so thresholds say nothing about 90%+ of app code. Add RTL screen tests for booking/payment. Driver-app is healthier.
- **T5 — The loadtest harness is real but has never run** (blocked on the missing staging env). It's a genuine two-sided sim with SLA assertions pinned to the CLAUDE.md table — wire it to a scheduled workflow once staging exists.
- **Regression tests to write with the fixes:** wallet-pay on a taxed ride (C5), dispute refund cap = grand_total (C6), safety check-in fires on a long trip with intervening updates (C7), post-refresh-second-401 does not loop (C2), transient refresh failure keeps the session (C3).

---

## 📈 Manager's Verdict

**Overall code health: strong and above-stage.** This is not a codebase that needs a rescue; it's one that needs a focused hardening pass before public launch. The engineering discipline is visible — conventions are documented and mostly followed, money math is Decimal-correct and idempotent, the security model is sound, and the sprint log shows real P0 follow-through. The reconciliation cron, RLS-first migrations, and SLA-pinned loadtest harness are all *ahead* of what a typical seed-stage rideshare has.

**The gap is between "correct" and "operable under load and failure."** Three themes:

1. **A short list of correctness bugs that quietly degrade trust** — broken wallet payments (C5), under-refunds on disputes (C6), a defeated safety timer (C7), and a random-logout-on-flaky-network (C3). These are not architectural; they're the kind of bugs that generate support tickets and erode confidence one user at a time. Fix these first — they're small, testable, and high-signal.
2. **A data-access layer that has outgrown its tooling.** PostgREST-over-HTTP-through-a-thread-pool is the one architectural decision that will actively fight the SLA table under load. It's invisible at launch volume, which is exactly why it needs a plan *now* — moving hot paths to asyncpg is the highest-leverage engineering investment on the board.
3. **Operational-readiness debt that is cheap today and impossible to retrofit onto old clients** — staging, forced-upgrade gate, kill switches, coverage enforcement, external monitoring. The team has already *identified* every one of these in ACTION_ITEMS.md; the finding is simply that they remain open and should be treated as launch-gating, not backlog.

**Recommended sequencing (no code written yet — this is the plan):**

- **This week (correctness, ~1-3 files each):** C5 wallet grand_total, C6 dispute grand_total, C7 safety-loop column, C1 admin XSS, C2/C3 client refresh loop + transient-logout. Each ships with a regression test.
- **Pre-launch (operability):** E1 sanitizer copy + E2 dispute-swallow + E3 loop alerting; T1 coverage enforcement + T2 real E2E gate; delete the three dead-code landmines; fix the deploy-pipeline drift; stand up staging (E1-ops) → forced-upgrade gate (E3) → kill switches (E5).
- **Next quarter (scale):** asyncpg hot paths → PostGIS dispatch/surge → OpenTelemetry → Redis Streams for the loop fleet, in that order. Each is independently shippable and independently valuable.

**Do not lose the differentiators while hardening:** 0% commission, capped/transparent surge, Canadian-regulatory-first design, and contractor-not-employee framing are the product. Several "gaps" versus Uber/Lyft are deliberate and correct — protect them.
