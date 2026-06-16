# Spinr — Engineering Director Code & Architecture Teardown

_Read-only review. 2026-06-16. Branch `claude/epic-planck-cr8l91`. No code modified._

Scope: backend (FastAPI dispatch/fare/payments/loops, auth/security/telemetry),
mobile apps (rider/driver, shared), admin-dashboard, testing & CI. Findings are
grounded in the actual source with `file:line` citations, then benchmarked
against Uber/Lyft engineering practice, and closed with a prioritized plan.

> **Headline:** This is a mature, security-conscious codebase — well above the
> median pre-launch startup. The obvious holes (JWT trust model, OTP hashing,
> Decimal money, Stripe idempotency, HttpOnly admin tokens, PIPEDA geohashing)
> are already closed and tracked. What remains is a **small set of real
> correctness bugs** (one money path), **systematic-but-fixable gaps** (per-domain
> coverage gates, runtime RLS tests, staging env), and **client-side maturity
> debt** (dead resilience infra, untyped WS handlers, redundant polling, a11y).

---

## 🚨 Critical Issues & Security Flaws

| # | Severity | Finding | Location | Why it matters |
|---|---|---|---|---|
| C1 | **HIGH** | `confirm_payment` writes `payment_status = intent.status` after only checking `metadata.user_id` — it never verifies the PaymentIntent's `ride_id` matches *this* ride, nor that `amount_received` matches the ride's owed total. A rider can confirm a cheap/mismatched intent against an expensive ride. The create paths are guarded by `_authoritative_ride_charge`; **confirm is not.** | `routes/payments.py:392-398` | Direct revenue-loss / free-ride path. The reconcile loop only flags it *after the fact* — and (see C2) its amount check is itself broken, so the false-positive noise buries the real discrepancy. |
| C2 | **HIGH** | Stripe ↔ DB reconciliation computes `expected_cents` from `ride["fare"]`, but the authoritative charge everywhere else is `grand_total + tip`. Fees/GST/PST/tip are excluded, so **every multi-line ride trips `DB_PAID_AMOUNT_MISMATCH` falsely** and a real discrepancy is invisible. | `stripe_reconcile.py:186-205` vs `payments.py:69-73` | The one control that would catch C1 is effectively non-functional. |
| C3 | **HIGH** | Dispatch driver-claim leak: if the process crashes/restarts between `claim_driver_atomic` and the `ride_offers` bulk insert, claimed drivers stay `is_available=False` with no offer row and no timeout handler (handler is scheduled only *after* a successful insert). They silently fall out of dispatch. The stuck-ride sweeper recovers the **ride**, not the orphaned **driver flags**. | `routes/rides.py:761→795-801, 976` | Slow supply erosion; violates the `is_available ⇒ is_online` recovery contract. Match-rate KPI degrades with no obvious cause. |
| C4 | MEDIUM | JWT error path interpolates the raw PyJWT exception into the 401 body — `detail=f"Invalid token: {str(e)}"`. Every other auth path was deliberately scrubbed to a static "Invalid token" to stop token-validation fingerprinting; this fallback is the one remaining leak. | `dependencies/__init__.py:295` | Lets an attacker probe which claim failed (alg/aud/exp/sig). OWASP A07. |
| C5 | MEDIUM | `payment_retry` single-replica Redis lock degrades to **no** mutual exclusion when Redis is the in-process fallback (each replica has its own dict, `SET NX` returns True everywhere). The atomic DB claim + Stripe idempotency key are the real guards, but the lock is advertised as the protection and isn't one in a no-Redis prod deploy. | `payment_retry.py:375-401` | Matches the documented "Redis transparency" footgun; combined with C3-style restarts, increases duplicate-work surface. |
| C6 | MEDIUM | `LIKE`/`ILIKE` `$regex` branch wraps user input as `f"%{v}%"` with **no `%`/`_` escaping**, unlike its `$or` sibling and `corporate_repo`. Bounded (service-role/RLS-scoped, no structural SQLi) but inconsistent and a mild ReDoS/over-match vector for admin search callers that don't `re.escape`. | `repositories/_base.py:502-507` | Defense-in-depth gap, not an open hole — but it's the kind of inconsistency that becomes a hole later. |
| C7 | LOW-MED | `get_cards`/`delete_card`/`set_default_card` swallow Stripe errors and report success — `get_cards` returns `[]` on any exception (a Stripe outage looks identical to "no cards"); `delete_card` clears the default even when the Stripe detach failed. This is the "log-and-continue on a payment error" anti-pattern CLAUDE.md explicitly forbids. | `payments.py:521-523, 717-718, 746-748` | Silent payment-state divergence between Stripe and UI. |

**Frontend resilience theater (two dead modules that misrepresent robustness):**

- `shared/api/offlineQueue.ts` — a complete persist-and-replay queue (~200 lines) whose `enqueueRequest()` is **never called anywhere**. The `OfflineBanner` is a separate cosmetic NetInfo component. Offline requests simply fail today. *And* if it were wired, FIFO replay of ride/SOS mutations has **no idempotency keys** → double-execution risk. Either wire it (with idempotency) or delete it.
- `shared/api/cachedClient.ts` — 299 lines, imported by nothing, and reads the access token from `SecureStore`/`localStorage` while `authStore` keeps it **in-memory only** → every request through it would 401. Dead and stale. Delete.

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**Strong foundation:** loguru→Sentry bridge delivers ERRORs (`server.py:384`), `send_default_pii=False` (`server.py:377`), analytics hard-rejects raw lat/lng and accepts geohash only (`utils/analytics.py:351-371`), break-glass admin path fails closed and audits every attempt, OTP SHA-256 + constant-time compare. PII discipline at the reviewed call sites is genuinely good (phone→last4, email→sha256, geohash-only).

**Gaps:**

1. **No central Sentry scrubber (`before_send`).** PII safety depends entirely on every future `logger.error` call site staying clean, because the loguru sink forwards raw `record["message"]` verbatim (`server.py:384-390`). One careless `logger.error(f"...{phone}...")` ships PII. A `before_send` regex backstop is ~20 lines and removes that whole class of risk.
2. **Two overlapping exception handlers** — `cors_exception_handler` on bare `Exception` (`core/middleware.py:524-562`) competes with `register_exception_handlers` (`server.py:245`). Risk that a future raised exception's sensitive `.detail` is reflected to the client before the sanitizer runs. Consolidate to one handler whose 5xx path always emits a generic body.
3. **`/metrics` may be unauthenticated in prod** — when `METRICS_AUTH_TOKEN` is unset it serves Prometheus data to anyone and only logs a warning; it is **not** in the `_validate_production_config` fail-fast set (`server.py:182-195`). Exposes error rates, traffic, circuit-breaker state.
4. **Client surfaces raw backend `detail` strings to users.** The well-built `SpinrApiError` (with `messageKey`/`actionHint` i18n) is thrown by `client.ts`, but stores bypass it: `authStore.ts:371,444,562` and `useDriverDashboard.ts:1184,1189` show `error.response?.data?.detail` verbatim — a FastAPI validation array or "Database operation failed" can reach an alert. The UX investment isn't realized on these screens.
5. **Client errors swallowed to `__DEV__`-only logs** — `authStore.ts` hides `fetchDriverProfile`/`refreshProfile`/auto-register failures behind `if (__DEV__) console.log(...)` (lines 307, 382, 419, 426). In production these produce **no signal at all** — below even the `warning`-and-continue the project forbids. A driver who lands on a disabled GO button leaves no diagnostic. Route to `captureException`.
6. **No React error boundary on the hot paths in a useful place** — `ErrorBoundary` exists but the fallback has no `accessibilityRole="alert"`/live-region, and a malformed WS event can still white-screen ride flows.

**Verdict:** admin/back-end telemetry is above average; the leak risk is *future-proofing* (scrubber) not a current spill. The **user-facing** side is where raw technical strings still escape and where failures go dark.

---

## 🐢 Performance Bottlenecks & Optimizations

| # | Path | Issue | Location | Fix |
|---|---|---|---|---|
| P1 | Dispatch (`<2s` SLA) | N+1 **sequential** Redis round-trips — one `await _redis_get` per candidate driver for the offer-skip key; up to 500 serial RTTs inside the dispatch path. | `routes/rides.py:712-714` | Single `MGET`/pipeline. Same shape on per-driver `quest_progress` (`:881-888`) — batch via `.in_()`. |
| P2 | Surge engine | `surge_pricing` history INSERT fires **every tick for every area** regardless of change (the `service_areas` update is correctly gated, the history insert is not). Unbounded write/storage growth. | `surge_engine.py:217-231` | Insert only on multiplier **transition**. |
| P3 | Driver app | GPS handler runs heavy synchronous work **per fix** (every ~3s on-trip): `checkLocationIntegrity`, `checkMovementConsistency`, payload build, JSON stringify + WS send — all on the JS thread alongside map animation. Render is throttled; the integrity checks are not. | `useDriverDashboard.ts:582-657` | Sample integrity checks; move off the hot path. (Buffer cap of 500 and go-offline purge are already good.) |
| P4 | Rider app | **Redundant polling alongside WebSocket** — `driver-arriving.tsx:188` polls `GET /rides/{id}` every **5s with no `wsConnected` check**, while `useRiderSocket` already pushes the same transitions and re-fetches on each event. `ride-in-progress.tsx` polls at 15s/20s similarly. | `rider-app/app/driver-arriving.tsx:188` | Gate intervals on `!wsConnected`. Cuts backend load + battery materially at scale. |
| P5 | Rider app | Map screens pass new object/array literals (region, coords, marker arrays) every render with no `useMemo` → `react-native-maps` re-diffs every frame during the GPS storm. | tracking/ride screens | Memoize props + selectors. |
| P6 | Backend loops | Loop-watchdog registration is a hand-maintained literal and **already out of sync** — omits `safety_checkin`, `reconciliation`, `zoho_desk_sync`, `loop_watchdog` itself. A stall in those never alerts. | `core/lifespan.py:354-372` | Derive `_WATCHDOG_LOOP_NAMES` from the spawned-task registry. |
| P7 | Re-dispatch | `match_driver_to_ride` re-dispatch tail + `_dispatch_retry(delay=10)` has **no attempt/depth cap** — a persistently empty market self-reschedules per ride (re-reading the full driver table each cycle) until the 5-min sweeper cancels. | `routes/rides.py:730, 1270` | Bound re-dispatch count explicitly. |
| P8 | Lists | Chat (`chat-driver.tsx:196-230`) renders all messages via `ScrollView + .map` (no cap, no virtualization). The team already uses `FlatList + onEndReached` correctly in `activity.tsx` — apply the same pattern. | `rider-app/app/chat-driver.tsx` | `FlatList`/`FlashList`. |

Already fixed and verified (don't redo): breadcrumb batching + completion flush, ETA movement gate, dispatch pushes off the request path, estimate polyline overlap, partial dispatch index `idx_drivers_dispatch_ready`.

---

## 💡 Tech Stack & Architecture Recommendations

The stack (FastAPI + Supabase/Postgres+RLS + Redis + Stripe + Expo RN + Next.js, Fly/Railway dual-deploy) is **well-chosen and coherent** for a Saskatchewan-first 0%-commission model. Recommendations fill gaps, not replace foundations:

1. **Runtime-validate the API boundary on the client (zod/io-ts).** Typed WS payloads exist in `shared/types/api/wsEvents.ts`, but the live handlers (`useRiderSocket.ts:71`, `useDriverDashboard.ts:678`) run on `data: any` and ignore them. A backend contract change fails **silently at runtime**, not at compile time, on the most safety-critical paths (ride-state, offers, coords). Parse-don't-validate with zod at the WS/HTTP boundary; derive types from the schema. *(Uber/Lyft both enforce schema contracts — protobuf/Thrift — between services and clients; this is parity-level, not gold-plating.)*
2. **Idempotency keys on all non-read mutations, not just some Stripe paths.** Generic ride/rating/profile writes have none, which is what makes any future offline-queue or client-retry dangerous. Add an `Idempotency-Key` header + a server-side `request_idempotency` table (pattern already proven by `stripe_events`).
3. **Stand up a staging environment (E1 in the backlog).** `main` → prod with no intermediate is the single biggest process gap. It's a prerequisite for load testing (E2 — harness already built), migration rehearsal, DAST (E6), and a11y E2E (E11). This is what Uber/Lyft call a "canary/pre-prod" gate; you have zero today.
4. **Redis-backed cross-replica primitives** for the WS per-user rate limit (`socket_manager.py:27-37`, per-replica only today) and the payment-retry lock (C5). `INCR`+`EXPIRE` with in-process fallback.
5. **PostGIS for surge + dispatch geo** (D1) — `surge_engine.py` caps at 500 drivers with Python point-in-polygon; push to server-side `ST_Contains` as supply grows. Lower latency, no cap.
6. **OpenTelemetry tracing** (D2) — `X-Request-ID` propagation exists; full distributed tracing becomes worth it the moment multi-replica latency debugging gets painful. Not yet gating.
7. **Kill switches / feature flags** (E5) — `app_settings` covers config but there are no documented kill switches for surge / scheduled dispatch / promo / corporate billing. A misbehaving loop should be disable-able in seconds without a deploy. Uber/Lyft run everything behind flags; this is a cheap, high-leverage add.
8. **Forced-upgrade gate for mobile** (E3) — no `min_supported_version`. Old binaries will eventually hit changed APIs. Impossible to retrofit onto clients already in the wild; add now.

---

## 🛠️ Maintainability & Code Smells

- **Dead/misleading infra:** `offlineQueue.ts` (never called) and `cachedClient.ts` (never imported, stale-token) — 500 lines implying resilience the app doesn't have. Delete or wire.
- **`any` density concentrated in load-bearing code:** apps lean heavily on `any` (rider 282, driver 222, admin **472**; shared a disciplined 14). Worst where ride/payment correctness lives — WS handlers, `mapRef`/`locationBufferRef: any[]`, `chatMessages.map((m: any)…)`. The shared-package sweeps (#551/#313) show the team can do this; the apps need the same pass, admin-dashboard first.
- **Hand-maintained registries that drift:** `_WATCHDOG_LOOP_NAMES` (P6) and the loop-import severity inconsistency (`safety_checkin`/`reconciliation`/`stripe_reconcile` use `logger.warning` on import failure while peers use `logger.error` — a dropped safety loop boots silently).
- **Fragile truthiness in claim correctness:** `claim_driver` falls back to `getattr(..., 1)` (treats unknown shape as success); any change to what `update_one` returns would silently double-offer a driver. Claim correctness shouldn't depend on a default-1.
- **Surge cap reported uncapped in admin view:** `get_surge_status` returns raw stored `surge_multiplier` without the `min(…, SURGE_CAP)` clamp the fare path applies — admin can advertise a >2.5× value that would never be charged.
- **Three near-duplicate API client/store layers** across rider/driver/shared invite drift; `shared/` is the source of truth but apps still carry local logic.

---

## 🧪 Testing & QA (missing edge cases)

**Strong:** ~190 backend `test_*.py`, real concurrency coverage (CAS acceptance race, wallet/promo concurrency, replay-safe loops), Stripe happy-path webhooks, and a CI with more gates than typical (Codecov, decoupled CVE audit, Trivy, security-gates, migration-check, post-deploy smoke).

**Top gaps, ranked:**

1. **No per-domain coverage enforcement.** CLAUDE.md mandates payments/fare/crypto ≥90% and rides/dispatch ≥80%; the only real gate is a **global 60%** in `backend/pytest.ini` (real baseline ~53.8% per the ratchet comment). Nothing in CI enforces the documented minimums. *(= ACTION_ITEMS A1, "single biggest remaining gap".)*
2. **`.coveragerc` omits real logic from the denominator** — `documents.py` (gates Period 1+!), `geo_utils.py`, `validators.py`, `schemas.py` — so reported 60% overstates true coverage.
3. **Stripe dispute webhooks (`charge.dispute.created`/`closed`) have ZERO tests**; `charge.refunded`/`account.updated` barely covered. CLAUDE.md: "every Stripe webhook type before production." These are money-reversal paths.
4. **RLS allowed/denied never tested at runtime** — only static SQL-string assertions; everything mocks `db_supabase.supabase`. The "integration tests against a throwaway schema" tier isn't wired in CI. The "both allowed and denied paths" requirement is unmet at runtime.
5. **State-machine matrix has holes** — `test_ride_state_machine.py` doesn't directly assert offer-timeout release (`driver_assigned → searching`), `scheduled → searching`, or `searching → driver_assigned`; some assertions reference legacy states (`requested`, `en_route`).
6. **E2E runs against a non-existent backend** — the Playwright `e2e-test` job points at `localhost:8000` but **nothing starts a backend there**, and it's `continue-on-error: true` on PRs → false confidence + flakiness.
7. **No load/perf testing against the SLAs** (dispatch <2s, WS fan-out <100ms) — only in-process micro-benchmarks. Harness is built (E2) but **blocked on staging (E1)**.
8. **No automated a11y in CI** despite WCAG 2.1 AA being a regulatory mandate — `axe-core` is in admin devDeps but unwired (E11). Mobile a11y is sparse: only 15/42 rider and 9/31 driver `.tsx` use any accessibility prop; `SOSButton` is exemplary but the bulk of touchables are unlabeled.

---

## 📈 Manager's Verdict — Overall Code Health

**Grade: B+ / "launch-capable with a short, sharp punch-list."** This is the top decile of pre-launch rideshare codebases I'd review. The team has done the hard, unglamorous safety work — JWT trust model, Decimal money, Stripe idempotency on charges, PIPEDA geohashing, append-only audit/insurance tables, fail-fast prod config, MFA + break-glass, a documented migration-conflict incident with a runbook. The discipline (CLAUDE.md, sprint tracking, ACTION_ITEMS, graphify) is genuinely better than most Series-B engineering orgs.

**vs. Uber/Lyft:** Functionally at parity for a single-province launch (dispatch, surge with a *tighter* 2.5× cap, insurance-period modeling, corporate billing, WAV). The gaps vs the incumbents are **operational maturity**, not features: staging/canary, load-tested SLAs, schema-contract enforcement client↔server, feature-flag kill switches, runtime RLS tests, and a DAST/pentest before public launch. Those are exactly what 50→500-engineer scale forces; you can adopt them deliberately rather than reactively.

**What I'd actually block launch on (P0):**
1. **C1** — `confirm_payment` ride/amount binding (free-ride path). Real money bug.
2. **C2** — fix reconcile to use `grand_total + tip` so the C1 backstop works.
3. **C3** — dispatch driver-claim leak recovery (supply erosion).
4. **A1** — per-domain coverage gates on the money paths (otherwise C1/C2-class bugs recur).

**Fast-follow (P1, pre-public-launch):** C4 (token leak), C5 (retry lock), Sentry `before_send` scrubber, dispute-webhook tests, staging env (unblocks load/DAST/a11y), client raw-`detail` leakage, delete the two dead client modules, gate WS-redundant polling.

**The plan, sequenced:**
- **Week 1 — money correctness:** C1 + C2 + regression tests, then ratchet A1 coverage gates on `payments.py`/`fare_service.py`.
- **Week 1-2 — dispatch & telemetry:** C3 driver-flag recovery, P1/P7 dispatch batching + retry cap, Sentry scrubber, watchdog registry auto-derivation (P6).
- **Week 2 — security cleanup:** C4 static 401, C5 Redis lock + WS rate-limit cross-replica, `/metrics` fail-closed, C6 LIKE-escape consistency.
- **Week 2-3 — process maturity:** stand up staging (E1) → run the built load harness (E2) → wire DAST (E6) + a11y (E11) into CI; add runtime RLS integration tests + dispute-webhook tests.
- **Week 3+ — client hardening:** delete dead infra, zod at the WS boundary, route swallowed client errors to Sentry, idempotency keys on mutations, a11y label pass, `any` sweep (admin first).

None of this is architectural rework — it's a finishing pass on an already-solid platform.
