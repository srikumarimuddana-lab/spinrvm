# Spinr — Engineering-Director Architecture & Code Review

**Date:** 2026-06-18
**Scope:** Full read-only teardown of all five surfaces — `backend/` (FastAPI), `rider-app/` & `driver-app/` (RN/Expo), `admin-dashboard/` (Next.js), `shared/` (TS). ~140k LOC backend Python (473 files), 252 backend tests, 223 migrations, 4 TS surfaces.
**Benchmark:** Compared against market-leader ride-share architecture (Uber/Lyft) where relevant.
**Method:** Five parallel read-only audits (security, error/telemetry, performance, architecture, frontend/testing). All findings cite `file:line` and were verified against actual code. **No files modified.**

> **Headline:** This is a *materially more mature* codebase than its size suggests. Most classic ride-share P0s are already closed — Decimal-only money, Stripe idempotency, JWT role re-read from DB, OTP hashing + lockout, atomic ride-accept claim, PostGIS spatial index, a DB circuit breaker, Sentry PII scrubbing, a 20-job CI pipeline. The remaining risk is **not** "missing fundamentals." It is concentrated in four places: (1) a **Firebase auth path that is weaker than the JWT path** on revocation/single-device, (2) **synchronous Stripe/Twilio SDK calls blocking the async event loop**, (3) a **single-process + 16-loop-per-replica scaling model with no worker tier**, and (4) **frontend reliability/i18n plumbing that is built and tested but not wired in.**

---

## 🚨 Critical Issues & Security Flaws

### C1 — Firebase ID tokens are not checked for revocation in the hot auth path *(Critical)*
`backend/dependencies/__init__.py:230` calls `firebase_auth.verify_id_token(token)` **without** `check_revoked=True`, whereas login (`routes/auth.py:695`) does pass it.
**Why it matters:** A Firebase session disabled/revoked server-side (account compromise, admin disable) keeps authenticating on every request until the ~1h token naturally expires. The app's own `token_version` revocation is honored, but a pure Firebase-console revocation is not — a real gap for "my account was hacked" flows.
**Fix:** Pass `check_revoked=True` here; if latency matters, gate it behind a short Redis cache keyed on the token's `auth_time`/uid.

### C2 — Firebase auth path silently bypasses single-device session enforcement *(Critical)*
`dependencies/__init__.py:276-279` — the session check is skipped when the token carries no `session_id` claim, which is the normal case for Firebase tokens (they aren't minted by `create_jwt_token`). The JWT path at `:337` is stricter and *would* reject a missing session.
**Why it matters:** `current_session_id` rotation (logout, new-device login) does not invalidate Firebase-authenticated sessions, defeating single-device login. Combined with C1, a Firebase session is effectively un-revocable short of bumping `token_version`.
**Fix:** Decide the policy explicitly — either require+compare `session_id` for Firebase, or document that Firebase sessions rely on `token_version` + `check_revoked` (C1) as the revocation mechanism.

### C3 — Webhook `payment_intent.succeeded` does not verify captured amount covers the ride total *(High → treat as Critical-money)*
`backend/routes/webhooks.py:299-324` sets `payment_status="paid"` from `meta["ride_id"]` without checking `amount_received` vs the ride's `grand_total`. The interactive `confirm_payment` path *does* enforce this underpay guard (`payments.py:410-430`) — but the **webhook is the authoritative settlement path Stripe retries**, and it trusts metadata.
**Why it matters:** A PaymentIntent created for a lower amount (stale/other intent carrying this `ride_id`, or pre-fix tampering) that later succeeds marks an expensive ride fully paid. The guard exists in the path that *doesn't* win under retries.
**Fix:** Mirror the `received < owed_cents` check inside the webhook before writing `paid`.

### C4 — `confirm_payment` reviewer mock-bypass settles real fares for free in production *(High)*
`backend/routes/payments.py:318-373` — when `current_user["phone"]` is in `review_login_map()`, a `pi_mock_*` intent is accepted **in production** and the ride is marked `paid` with **no amount verification** (unlike the real branch at `:410-430`). Bounded by the env-secret reviewer allow-list, but the design trusts that list is always emptied post-review, and settlement is only `logger.info` (no audit/alert).
**Fix:** On the mock path, verify the ride is a reviewer-owned test ride (or forbid mock settlement of any real fare); audit loudly.

### C5 — SES SNS suppression endpoint fails *open* in production *(High)*
`backend/routes/webhooks.py:1097-1112` — if `aws_ses_sns_topic_arn` is unset (settings live in the admin-editable `app_settings` table — easy to leave blank), `_topic_arn_allowed` returns `True` with only a `logger.warning`.
**Why it matters:** Email-suppression poisoning — a validly-signed SNS message from *any* topic can add victim addresses to `email_suppressions`, silently killing OTP/receipt delivery (DoS on account recovery). The signature proves "some topic," not *our* topic.
**Fix:** Fail closed in production (reject when the expected ARN is unset), matching the OTP-lockout fail-closed posture.

### C6 — Mobile refresh tokens stored in plaintext AsyncStorage *(High — verify against threat model)*
Driver/rider 30-day refresh tokens + user PII persisted via Zustand `persist` → **unencrypted AsyncStorage** (`shared/store/storage.ts`). Admin was correctly moved to HttpOnly cookies (sprint A-P0-1); mobile was not.
> **Note:** A later, deeper audit found the rider/driver model is actually **better than first reported** — access token is *memory-only* and only the *refresh* token persists, and on newer code it uses `expo-secure-store` (`authStore.ts:229-240`). **Action: confirm which path ships** — if any refresh token still lands in AsyncStorage, move it to `expo-secure-store` (Keychain/Keystore).

**Verified-good (no action):** Decimal-only money with `dollars_to_cents` (no float drift); atomic double-accept / cancel-after-start guards; JWT role re-read from DB (forged role claims ignored); `hmac.compare_digest` for OTP; production config guards (JWT length, weak-admin-password, Canadian region, CORS wildcard); `cancelled`-after-`in_progress` forbidden.

---

## 🛡️ Error Handling & Telemetry (User UX vs. Admin logging)

**Posture: production-grade.** Centralized 5xx sanitizer (`utils/error_handling.py:708-780`) scrubs any non-`ERR_*` detail to `"Internal server error"` + Sentry ID; `general_exception_handler` never emits tracebacks; backed by a regression test. Sentry has a PII scrubber (phone/email/GPS/postal), `send_default_pii=False`, a loguru→Sentry bridge promoting `domain`/`surface`/IDs as tags, and **all 7 documented `spinr_<domain>_<metric>_<unit>` Prometheus names are emitted at real call sites.** Graceful degradation is real: Supabase circuit breaker (5 fails/30s → 503, half-open probe), OTP fails *closed* on Redis loss, row-cache degrades to DB read.

Remaining gaps (mostly LOW, defense-in-depth):
- **Latent leak footgun:** `backend/documents.py:313/945/959` interpolate raw `{e}` into 500 `detail` — currently neutralized by the sanitizer, but would leak instantly if anyone lowered the status to 4xx. Fix at source.
- **`logger.warning(...)+continue` residue** on a few payment/dispatch/corporate-loop paths — escalate to `logger.error(..., exc_info=True)` + metric per CLAUDE.md.
- **`DatabaseError` root cause dropped:** several sites log `str(e)` (="Database operation failed") instead of `e.details['original']`. Standardize a `log_db_error(e, ctx)` helper.
- **Sentry `domain` tag not set on direct `capture_exception` calls** — wrap in a `capture(domain, **tags)` helper.
- **Raw GPS in some debug logs** (`dispatch_service`, driver location path) — geohash before logging; add a lint gate.
- **Frontend (shared TS):** `client.ts:556-569` `[API-ERR]` console output is **un-gated** (prints in production device logs, retains raw `data` body). Gate behind `__DEV__` / route through `shared/utils/logger.ts`.

**CI guardrails to add:** grep-gate `detail=str(e)` / `detail=f"...{e}"`; `flake8-print (T201)`; a lat/lng-in-log-call lint rule.

---

## 🐢 Performance Bottlenecks & Optimizations

Against the documented P95 SLAs (dispatch <2s, fare estimate <300ms, settlement <1s, WS fanout <100ms, location write <150ms).

### P0 — Synchronous Stripe/Twilio SDK calls block the event loop *(highest-leverage fix)*
The DB layer correctly offloads via `run_sync` → `_DB_EXECUTOR`. **Stripe and Twilio do not** — they call the *sync* SDK directly inside `async def`, blocking the single event-loop thread for the whole round-trip and stalling every concurrent request/WS fan-out on that worker:
- **Pre-auth hold inline before dispatch:** `routes/rides.py:1953` → `utils/stripe_charge.py:380` (`stripe.PaymentIntent.create`, no `to_thread`), awaited *before* `insert_ride`/`match_driver_to_ride`. A 300–800ms Stripe RTT sits serially in front of dispatch → risks the <2s offer SLA.
- **Settlement charges:** `stripe_charge.py:204/210/575`, awaited inline at trip completion → risks <1s settlement + <500ms webhook SLAs.
- **SOS Twilio loop (safety-critical):** `routes/rides.py:4688` loops emergency contacts, each `await send_sms` → sync Twilio SDK, serial. N contacts × ~500ms blocks the loop during an emergency.
**Fix:** Wrap every sync Stripe/Twilio call in `asyncio.to_thread(...)` (the FCM path at `features.py:1338` already does this). Fan out SOS sends with `asyncio.gather` over `to_thread`. Evaluate moving the pre-auth hold off the dispatch critical path.

### P0 — Unguarded background loops cause duplicate side-effects across replicas
All 16+ loops in `core/lifespan.py` run on **every** replica. These lack a replay-safety guard:
- `utils/corporate_low_balance.py:53-67` — read-then-write + *unconditional* `mark_low_balance_notified` UPDATE → N replicas = N duplicate billing emails. Fix: conditional UPDATE … RETURNING, gate the send on the claimed row.
- `utils/safety_checkin_loop.py:103-183` — Redis read-then-`set` (not `SET NX`) + unclaimed `safety_incidents` insert → duplicate "Are you okay?" pushes and **duplicate safety incidents**. Fix: `redis_set(..., nx=True)` claim or atomic insert.
- `utils/corporate_autotopup.py:125-143` — money path guarded *only* by Stripe idempotency key (single layer). Fix: add a DB claim.
> Reference-correct loops to copy: `push_retry.py` (atomic lease CAS), `stuck_ride_sweeper.py` (bulk atomic claim), `reconciliation`/`retention_purge` (Redis leader lock).

### P1 — N+1 Supabase reads on the dispatch hot path
- `services/dispatch_service.py:543-575` — **3 sequential reads per candidate driver** (drivers/users/vehicles) in a loop → 60 round-trips for 20 candidates. Batch with `.in_()` (the codebase already uses this in `get_rides_by_ids`). **Single highest-impact, lowest-effort win.**
- `routes/rides.py:922-929` — per-driver `quest_progress` query inside the notify loop. Batch before the loop.
- `utils/driver_claim_reaper.py:106`, `utils/document_expiry.py:109` — per-driver reads over full scans → batch with `.in_()`.

### P1 — Driver-location write does a read-modify-write on the hottest path
`routes/drivers.py:4317-4361` — `SELECT` then separate `UPDATE` on every GPS ping → doubles DB ops on the highest-frequency write. Fix: single `UPDATE … RETURNING`, or push to Redis `GEOADD` and flush async.

### P1 — `run_sync` thread pool is the global concurrency ceiling
`db_supabase.py` routes *every* Supabase call through a bounded pool (64 workers). A burst of slow admin/reporting queries can starve the dispatch hot path. Fix: segment executors (small dedicated pool for hot dispatch/location vs. admin/reporting), or move hot paths to `asyncpg`. Push admin aggregations into SQL (`count`/`sum`/`group by` RPC) instead of fetch-then-reduce-in-Python (`routes/admin/rides.py`, `utils/analytics.py`).

**Verified-good (no action):** fare estimate is structurally protected (haversine + arithmetic duration; Directions polyline offloaded + `wait_for(…,0.5)`); location-write WS path uses cache-only ETA + off-loop refresh; WS fan-out is *targeted* per-user (no broadcast-to-all in production), per-message 2s timeout; row-level Redis cache (30s TTL) fronts hottest reads.

---

## 💡 Tech Stack & Architecture Recommendations (vs. Uber/Lyft)

What already exists and surprised the audit (don't rebuild): PostGIS + GIST spatial index (`ST_DWithin`/`ST_Distance`, `match_and_claim_driver` with `FOR UPDATE SKIP LOCKED`), DB circuit breaker + Redis row cache, offline queue, TanStack Query (driver app), daily Stripe reconciliation, loop watchdog, 20-job CI with a Postgres service container.

The real gaps vs. a market-leader platform:

1. **No async worker/queue tier — the single biggest gap.** All deferred work (`asyncio.create_task` at `rides.py:195/771/1018/2908`) and all 16+ loops run *inside the web process*. Zero hits for Celery/Arq/Kafka/SQS. A deploy drops in-flight `create_task` work (the codebase even ships `stuck_ride_sweeper`/`driver_claim_reaper` to recover it). **Fix:** **Arq** (Redis-native, lightest lift) or Celery for dispatch retries, push delivery, snapshot rendering, reconciliation — then delete several recovery loops.
2. **No event bus / CDC.** State transitions fire-and-forget WS events; no durable ordered log. Uber/Lyft run Kafka so dispatch/pricing/fraud/analytics share one stream. **Fix:** Kafka/Redpanda, or at least Postgres logical replication / durable Redis Streams.
3. **PostGIS installed but not the primary path.** Spatial count is behind a default-OFF flag (`SURGE_SPATIAL_COUNT`); dispatch *ranking/ETA* still uses Python haversine over a fetched candidate set. **Fix:** promote PostGIS to primary; evaluate **H3** (Uber's hex grid) for surge supply/demand bucketing.
4. **Single-process + single-DB scaling ceiling.** One FastAPI app, `uvicorn --workers 4`, no load balancer (failover = manual Cloudflare DNS flip), all durable state in one Supabase Postgres (SPOF + bottleneck), shared cross-region Redis (cross-region SPOF). supabase-py is synchronous → concurrency capped by thread count, not async I/O. **Fix:** PgBouncer/Supavisor pooling + read replica for admin/analytics; move hot dispatch reads to Redis.
5. **No distributed tracing.** Strong metrics/Sentry but no OpenTelemetry — you cannot trace request→DB→Stripe→WS to actually measure the P95 SLAs the docs commit to. **Fix:** OTel SDK + OTLP backend (Tempo/Honeycomb).
6. **Hand-rolled SQL migration runner (223 files, documented duplicate prefixes, rollback "on paper").** Works but fragile. **Fix:** Sqitch or Atlas (SQL-first, dependency-ordered, real verify/revert).
7. **No feature-flag service** (flags are ad-hoc env vars). **Fix:** Unleash (self-hosted, data-residency-friendly) or Flagsmith for runtime toggles/percentage rollouts/kill switches.
8. **Duplicate route mounts** (`server.py:334-375` mounts routers at `/api`, `/api/v1`, root) **split SlowAPI rate-limit counters per prefix** — a real correctness smell. Retire behind a coordinated mobile release.

---

## 🛠️ Maintainability & Code Smells

- **God files** dominate: `routes/drivers.py` **6,730 lines**, `routes/rides.py` **5,518**, `routes/admin/rides.py` 2,851, `features.py` 1,639 (mixes pricing + support + FAQ). `rides.py` alone holds the state machine, dispatch trigger, offer timeout, snapshot rendering, push helpers, retry logic. **Continue the in-progress `repositories/`+`services/` extraction**; target <800 lines/route file, routes as thin HTTP adapters.
- **Dual-import `try/except ImportError`** in every module doubles import surface, defeats static analysis, and drags `# type: ignore` litter. Intentional today, but the clean fix is one entrypoint convention + packaging, deleting hundreds of blocks.
- **`db_supabase.py` is a re-export shim** for ~66 symbols so "40+ callers" don't break — the repository split stalled mid-flight. Finish it and delete the shim.
- **`_WATCHDOG_LOOP_NAMES` has already drifted** out of sync with the spawned loops (`safety_checkin`, `suspension_reactivation`, `zoho_desk_sync`, `preauth_capture`, `driver_claim_reaper` spawned but not watched → can go stale silently). **Generate the watchdog list from the spawn registry.**
- **Frontend reliability plumbing built-but-unwired** (the biggest frontend finding):
  - `shared/api/offlineQueue.ts` has **0 call sites** — there is effectively *no* offline write handling despite the code. Wire it in or delete it.
  - `rider-app/lib/alert.ts::showErrorAlert` (i18n + PII-safe + action-hint) has **0 call sites**; ~20 rider screens render raw backend `detail` strings directly (`login.tsx:54`, `wallet.tsx:122`, `ride-status.tsx:562`, …). Route screen errors through it.
  - `shared/api/cachedClient.ts` is orphaned dead code with a *stale insecure* token model (reads `localStorage` `auth_token`). Delete it.
- **Type debt:** `admin-dashboard/src/lib/api.ts` has **181 `any`** — the admin API client (money/ride/driver payloads) is almost entirely untyped. Surface totals: admin ~560, rider ~284, driver ~235, **shared 15 (clean)**. `@ts-ignore` count is only 4 — the escape hatch is well-controlled. Start the typing push at `admin/src/lib/api.ts`.
- **Rider WebSocket weaker than driver:** no max-retry cap (reconnects forever), jitter too tight (~1.7% → thundering-herd on restart), no heartbeat watchdog (half-open socket strands rider on stale state). Port the driver-app guards.

---

## 🧪 Testing & QA (Missing Edge Cases)

Backend suite (252 tests) is notably stronger than the frontend's. Insurance periods, surge tiers/cap, GST/PST line items, promo, decimal rounding, admin RBAC (allow+deny), OTP/MFA, ride-accept race are all well covered. Gaps:

- **`services/fare_service.py::calculate_fare` has no direct test** — only exercised via routes; the **minimum-fare floor** (`max(subtotal, minimum)`) is never asserted anywhere. Add a direct test.
- **Two handled Stripe webhooks have zero tests:** `charge.dispute.closed` (won→paid vs lost→dispute_lost) and `account.updated` (Connect KYC mirror). `charge.refunded`/`dispute.created` test only the cents math, not the full side-effects (status flip, `stripe_disputes` insert, admin WS broadcast). 3DS/SCA (`payment_intent.requires_action`) and `radar.early_fraud_warning` — verify.
- **Three ride transitions unpinned:** `scheduled → searching`, `searching → cancelled` (auto, no drivers ~5min — untested anywhere), and assert the `driver_assigned → searching` offer-timeout release-back.
- **RLS denial paths entirely unverified** — RLS lives only in migrations; backend bypasses it by design, so there's no automated allowed/denied test. Add at least one anon-key denial test per user-data table.
- **Frontend tests thin on the load-bearing paths:** `client.ts` 503-retry, `RateLimitError`/`Retry-After` parsing, `extractError` ladder are untested; WS backoff/cap/jitter/heartbeat/AppState untested; offline replay untested (and dead). SOSButton 4-state retry — confirm the regression test asserts "never green until backend 200."
- **Edge cases:** unknown `ride.status` value (CLAUDE.md says treat as contract violation — no test enforces it); `America/Regina` no-DST scheduled-dispatch boundary; even-cents fare split ($10.00/3 — no penny lost/created); empty states (no drivers, expired card, null geocode, denied location permission); client double-tap Book/Confirm debounce.

---

## 📈 Manager's Verdict

**Overall code health: B+ / strong.** This is a disciplined, security-conscious codebase that has clearly been through multiple hardening sprints — the audit trail in `sprint-current.md` is real, and spot-checks confirm the P0s it claims are genuinely fixed. The money discipline (Decimal-only, idempotency, reconciliation), the regulatory posture (PIPEDA scrubbing, SK retention, insurance-period audit), and the observability foundation are above the bar for a pre-launch product and competitive with the fundamentals at a market leader.

**The gap to "Uber/Lyft-grade" is architectural maturity, not correctness:** there is no async worker tier, no event bus, no distributed tracing, and the whole system is a single FastAPI process with a single Postgres SPOF and 16 loops replicated per pod. That model is fine for a Saskatchewan-first launch but is the wall the platform will hit on scale — and it should be addressed *before* multi-market expansion, not after.

**If I owned this team, the next-30-days priority stack is:**

| # | Item | Type | Effort | Why first |
|---|---|---|---|---|
| 1 | C1+C2 Firebase `check_revoked` + single-device enforcement | Security | Low | Auth path materially weaker than JWT; un-revocable sessions |
| 2 | C3 webhook amount verification | Money | Low | Authoritative settlement path trusts metadata |
| 3 | P0 wrap Stripe/Twilio in `asyncio.to_thread` (+ SOS gather) | Perf/Safety | Low | Highest tail-latency leverage; SOS is safety-critical |
| 4 | P0 guard `corporate_low_balance` + `safety_checkin` loops | Correctness | Low-Med | Duplicate billing emails + duplicate safety incidents across replicas |
| 5 | P1 batch dispatch N+1 (`.in_()`) + drop location read-modify-write | Perf | Low | Protects the two tightest SLAs |
| 6 | Wire `showErrorAlert` into rider screens; decide offlineQueue (wire or delete); delete `cachedClient.ts` | Frontend | Low-Med | Built-and-tested reliability layer currently bypassed |
| 7 | Tests: direct `calculate_fare`+min-floor, `dispute.closed`/`account.updated` webhooks, 3 ride transitions, RLS denial | QA | Med | Closes the money + state + auth test gaps |

**Quarter horizon:** introduce **Arq** (worker tier — then delete recovery loops), add **OpenTelemetry** tracing, promote **PostGIS** to the primary dispatch path, add **PgBouncer + a read replica**, and begin breaking up `drivers.py`/`rides.py` as the repository extraction lands.

*Nothing here is launch-blocking on its own; items 1–4 are the ones I would not ship a public launch without.*
