# Spinr — Engineering Director's Architecture & Code Review

**Date:** 2026-06-19
**Scope:** Read-only teardown of backend (FastAPI), rider/driver apps (Expo RN), admin (Next.js), shared TS — benchmarked against rideshare market leaders (Uber/Lyft).
**Method:** Static review + targeted verification of the highest-severity claims against live code. Findings the agents over-called were discarded after verification (noted inline).

---

## TL;DR — Manager's Verdict (read this first)

**Overall health: B+ / strong.** This is not a struggling codebase. The P0 security/safety sprint is closed, money arithmetic is Decimal-clean, the WS fan-out is fleet-aware, error-to-user leakage is largely contained behind an i18n layer, and PII discipline in logs is real. The conventions in `CLAUDE.md` are unusually rigorous and are *actually followed* in the code I sampled.

The gap between Spinr and Uber/Lyft is **not correctness — it's surface area and operational maturity**: a 5,500-line god-file in the hottest path, a hand-rolled dispatch matcher where the market uses dedicated geospatial/streaming infra, and a few defensive timeouts missing on outbound calls. None of these are fires. They are the difference between "ships and works" and "scales to 10 cities without a 3am page."

**Verification note:** Of the 5 "Critical" backend findings my sub-agents raised, **the top one was wrong** — the alleged payment-race at `payment_service.py:525` is correctly guarded (the DB write is in a try/except that returns 503 before the success notification fires). I down-rated several others. The findings below are the ones that survived inspection.

---

## 🚨 Critical Issues & Security Flaws

Honestly: **no true criticals survived verification.** The sprint already burned them down. What remains are real-but-bounded correctness gaps:

| # | Finding | Location | Why it matters |
|---|---------|----------|----------------|
| C-1 | **TOCTOU on mid-trip stop edits.** Status is checked (read), then the fare-changing `update_one` runs with only `{"id": ride_id}` in the WHERE clause — not `status`. A ride completing/cancelling between check and write lets a fare mutation land on a terminal ride. | `rides.py:4426→4452` (add), `4485→4509` (remove) | Violates the repo's own "filter on status" race-guard convention (the one used correctly in ride-acceptance). Low probability (rider editing stops at the instant of completion), financial blast radius small, but it's a silent state-machine breach. |
| C-2 | **Offer-timeout re-search is not atomic.** `_offer_timeout_handler` gates on `status == DRIVER_ASSIGNED` (line ~1086) then writes `status = SEARCHING` (~1141) without re-asserting the state in the update filter. A driver accepting in that window can be clobbered back to `searching`. | `rides.py:1086 → 1136-1147` | Could strand an accepted ride. Same fix pattern as C-1. |
| C-3 | **No `asyncio` deadline on outbound Stripe / email / push / personal-WS sends.** Stripe's 30s socket timeout is set globally but not enforced per-call at the event-loop layer; `send_personal_message → _deliver_local` (`socket_manager.py:301`) has no timeout while `broadcast` does (2s). | `stripe_charge.py`, `payment_service.py`, `socket_manager.py:301` | A single hung upstream stalls a worker's event loop. **Verified real** for the WS path. Uber/Lyft wrap every egress in a hard deadline + circuit breaker as table stakes. |

**The "how" (no rewrites):**
- C-1/C-2: add the expected status to the update's filter (`{"id": ride_id, "status": {"$in": [...]}}`) and treat a 0-row result as "lost the race" → 409, exactly as ride-acceptance already does. One-line pattern, already proven in this repo.
- C-3: wrap egress in `asyncio.wait_for(..., timeout=N)`; for Stripe, also move the synchronous SDK call to a thread (`run_in_executor`) so a slow capture can't pin the loop.

---

## 🛡️ Error Handling & Telemetry

**This is a strength.** The architecture already separates user-facing copy from admin diagnostics:

- **User side:** `shared/api/client.ts` resolves errors to i18n keys (`messageKey`/`actionHint`) before any raw `message`; `rider-app/lib/alert.ts:showErrorAlert()` never surfaces a stack. 429s become a typed `RateLimitError` with retry-after for a countdown UX. SOS is exempted from the token-refresh dance so an emergency never blocks on auth.
- **Admin side:** `logger.error(..., exc_info=True)` sweeps already landed on payment/dispatch/safety paths (per sprint log); `DatabaseError` carries `e.details["original"]`; loguru→Sentry bridge is wired; Sentry `send_default_pii=False`.

**Leaks that remain (all Medium/Low):**
- `rider-app/app/payment-confirm.tsx:206` — raw `error.message` shown on a **critical** screen. Should route through `showErrorAlert({ error })`.
- `rider-app/app/login.tsx:54`, `saved-places.tsx:117`, `loyalty.tsx:140` — raw backend `detail`/`message` as last-resort fallback (no i18n).
- `admin-dashboard/src/lib/api.ts:169` — throws a bare `Error(msg)` instead of a structured class, so admin screens can't branch on error code (e.g. 3DS `action_required`).

**Gap vs. market:** there's no evidence of **structured request tracing** (OpenTelemetry spans across rider→backend→Stripe). Request-ID correlation exists (good), but distributed tracing is how Uber/Lyft actually debug a slow dispatch across services. See Tech Stack.

---

## 🐢 Performance Bottlenecks & Optimizations

| Finding | Location | Impact / Fix |
|---|---|---|
| **Surge supply count is an O(N) Python polygon scan** when PostGIS path is feature-flagged off. | `surge_engine.py:140-201` (`_SURGE_SPATIAL_COUNT` opt-in) | On a multi-thousand-driver area the 2-min surge loop CPU-starves. **Make PostGIS the default**, Python scan the fallback. |
| **N+1 in receipt generation** — rider, then driver, then driver's user fetched sequentially. | `payment_service.py:736-759` | Batch via `.in_()` / a view. Off the hot request path (email), so Medium. |
| **Dispatch presence-filter falls back to *all* online drivers** on Redis failure, silently and indefinitely. | `dispatch_service.py:296-311` | Correct as a soft-fail, but should trip an alert/metric (`spinr_dispatch_presence_filter_failed_total` exists — wire it to a 2-strikes alert) so a sustained Redis outage doesn't silently degrade match quality. |
| **Background-loop heartbeat recorded *after* a 5s restart sleep**, watchdog polls every 5 min. | `lifespan.py:141-153` | A crashed loop can be dark 5-10 min before alerting. Record heartbeat before the sleep. |

The documented SLAs (P95 dispatch < 2s, fare < 300ms) are sensible and the partial dispatch index (`idx_drivers_dispatch_ready`) shows the team already thinks this way. The risk is that the **matching engine is in-process Python** — fine for Saskatchewan launch, a known ceiling for multi-market scale.

---

## 💡 Tech Stack & Architecture Recommendations

The stack is modern and well-chosen (FastAPI, Supabase+RLS, Redis pub/sub, Expo SDK 54, Next 16). The honest gaps vs. Uber/Lyft:

1. **Dispatch / geospatial.** Hand-rolled haversine + Python ranking. The market uses dedicated spatial indexing (H3 hex grid, PostGIS `GEOGRAPHY` + GiST). **Recommend:** commit to PostGIS as the *primary* supply-count path now (the code already has the branch), and put H3 on the roadmap before city #2. *Why:* point-in-polygon over thousands of drivers per tick won't hold.
2. **Distributed tracing.** Add **OpenTelemetry** (FastAPI auto-instrumentation → Tempo/Honeycomb/Datadog). *Why:* you have metrics + Sentry but no single trace showing where a 1.8s dispatch actually spent its time across DB/Redis/FCM.
3. **Async job queue.** 16 background asyncio loops on every replica with hand-rolled claim-flags is clever but brittle (see heartbeat gap). The market-standard answer is a real broker — **Celery/Arq/RQ on Redis** or a managed queue — giving retries, dead-letter, and visibility for free. *Why:* replay-safety is currently a per-loop manual discipline; one missed claim flag = a double-charge.
4. **Outbound resilience layer.** No circuit breaker on Stripe/Twilio/Maps. Add `tenacity` (retry/backoff, partially present) + a breaker (`pybreaker`). *Why:* an upstream brownout currently degrades into event-loop stalls rather than fast-failing into queued retries.
5. **Feature flags.** Surge spatial path is a code constant. A flag service (LaunchDarkly/Unleash/Supabase-table-backed) would de-risk exactly these "default off in prod" footguns.

---

## 🛠️ Maintainability & Code Smells

- **`routes/rides.py` is 5,518 lines — the dominant smell.** `create_ride` alone is ~857 lines doing ~12 responsibilities (ban check → geofence → fare → surge-lock → corporate policy → pre-auth → promo → insert → polyline → dispatch). `match_driver_to_ride` ~413, `get_ride` ~277. *Why it matters:* every merge into this file is a conflict magnet, review fatigue hides bugs (C-1/C-2 live here), and the sprint already says "do not touch rides.py paths — active area." That warning is a symptom. **Plan:** extract `create_ride`'s phases into `ride_creation_service`, the triple WS-emit into `_emit_ride_status_change()`, and the duplicated offer-miss logic (`1095-1132` ≈ `1276-1299`, ~80% identical) into one helper. Do it incrementally behind tests, not as a big-bang.
- **Inline business logic in routes:** corporate surge-bypass (`2316-2325`, duplicated at `1611-1617`), airport surcharge, promo capping — all belong in `services/`.
- **String-literal ride states** (`"requested"`, `"en_route"` at `4141-4146`; `"driver_assigned"` etc. at `5486`) bypass the `RideStatus` enum — drift risk the docs explicitly warn against.
- **`shared/`** is in good shape; the remaining `any` types are mostly SDK refs (`errorReporting.ts:53,57` should adopt `@sentry` types).

---

## 🧪 Testing & QA (Missing Edge Cases)

Coverage discipline is codified and the E2E suite (`#266`) covers the lifecycle. Gaps to close:

- **Concurrency tests for C-1/C-2** — simulate a status transition landing between read and write on stop-edit and offer-timeout. The repo tests the ride-acceptance race but not these sibling paths.
- **Corporate settlement saga rollback** — assert the allowance is re-credited when the master-wallet debit *and* its compensation both fail (`payment_service.py:280-329`). Verify whether money can be stranded.
- **Upstream-timeout simulation** — no test forces a hung Stripe/Twilio to prove the (to-be-added) deadlines fire and degrade gracefully.
- **Surge zero/zero state** — `demand=0, supply=0` reports 1.0× rather than "no data" (`surge_engine.py:326`); decide intended behavior and pin it.
- **`ride-in-progress.tsx`** renders blank during the initial `fetchRide()` (no skeleton) — add a loading state + a test.

---

## 📈 Plan — Prioritized, shippable in ≤3-file commits

**Now (this/next sprint, low risk, high value):**
1. C-1 + C-2 atomic status guards on stop-edit + offer-timeout re-search (+ regression tests). *2 files each.*
2. C-3 timeouts: wrap `_deliver_local` send, Stripe calls, email/push in `asyncio.wait_for`. *Scoped.*
3. Flip surge supply-count to PostGIS-default. *1 flag + verify.*
4. `payment-confirm.tsx:206` → `showErrorAlert`. *1 file.*

**Next (de-risk scale):**
5. Begin `rides.py` decomposition — extract `_emit_ride_status_change()` and the offer-miss helper first (safe, mechanical).
6. Wire `spinr_dispatch_presence_filter_failed_total` to a 2-strikes alert; move loop heartbeat before the restart sleep.
7. Structured admin error class.

**Roadmap (before city #2):**
8. OpenTelemetry tracing.
9. PostGIS-primary + H3 on the dispatch path.
10. Move the 16 loops onto a real job broker (Arq/Celery).
11. Circuit breakers + feature-flag service.

**Bottom line:** Spinr is in materially better shape than most pre-launch rideshare codebases I've reviewed. The conventions are real and enforced. Spend the next two sprints on the four "Now" items and the `rides.py` carve-up, and you remove the only structural risks standing between this and a multi-market platform.
