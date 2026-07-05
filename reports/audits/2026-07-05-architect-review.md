# Spinr — Chief Architect Code Review & Remediation Plan

**Date:** 2026-07-05
**Scope:** Read-only teardown across `backend/`, `rider-app/`, `driver-app/`, `admin-dashboard/`, `shared/`
**Method:** Three parallel grounded audits (backend hotpaths, mobile/admin frontends, security/data layer), findings verified against actual `file:line`. Two headline items independently re-verified by hand.
**Benchmark:** Uber / Lyft engineering practice at equivalent stage.

> **Headline:** The platform is genuinely well-hardened — prior audit sprints closed the JWT trust model, admin RBAC, OTP flow, refresh-token rotation, Stripe idempotency/webhook verification, and financial-table RLS, and those are *correct*. This review is not a list of beginner mistakes. It is a short list of **real, still-open defects in the exact bug classes the team has been closing** — plus the architectural gaps that separate a strong pre-launch codebase from an Uber/Lyft-grade operation.

---

## 🚨 Critical Issues & Security Flaws

### C1 — Stale tax on distance recalculation → real overcharge + CRA remittance mismatch
`backend/services/fare_service.py:313–331` (`recalculate_fare_for_distance`)
**Verified by hand.** When actual trip distance diverges from the estimate by >0.1 km (the *common* case, wired from `routes/drivers.py:5007`), the function recomputes the metered subtotal `new_total_fare` (base + distance + time + booking + airport) for the real distance — but reuses the **booking-time** `tax_amount` unchanged (`:329`) and folds it straight into `new_grand_total` (`:331`), which `payments.py::_authoritative_ride_charge` bills via Stripe.
**Why it matters:** GST (5%) and PST (6%) no longer equal the tax on the fare actually charged. This is a live over/undercharge on every recalculated ride **and** a tax-remittance error — the receipt's tax line and the CRA-reportable amount disagree. This is exactly the "every charge maps to a disclosed line item" guardrail, broken silently.
**Root cause:** Tax was treated as a fixed pass-through instead of a function of the (mutable) subtotal.
**How to fix:** Recompute GST/PST off `new_total_fare`, rewrite `tax_breakdown`, then sum `new_grand_total`. Add a fare-branch test asserting `tax == round(subtotal * rate)` after recalculation. **This is a money path — CLAUDE.md mandates ≥90% coverage here.**

### C2 — `SECURITY DEFINER` promo RPC exposed to the anon key (Broken Access Control / IDOR)
`backend/migrations/51_promo_atomic_increment.sql:12` (`promo_increment_uses`)
**Verified by hand.** The function is `SECURITY DEFINER` with a pinned `search_path` (good) but has **no `REVOKE EXECUTE ... FROM PUBLIC, anon, authenticated`**. Supabase/PostgREST auto-publishes every `public` function at `/rest/v1/rpc/`, and the anon key ships inside the mobile bundles.
**Why it matters:** Anyone can `POST /rest/v1/rpc/promo_increment_uses` with an enumerated promo UUID, bypassing *all* backend auth, and burn each promo's `uses` up to `max_uses` — denying the promotion to real riders and corrupting redemption analytics. This is the identical exposure migrations 111/203/204/205 were written to close for the wallet/corporate RPCs; this one was **missed in that sweep**.
**How to fix:** New migration mirroring 205: `REVOKE EXECUTE ON FUNCTION promo_increment_uses(UUID) FROM PUBLIC, anon, authenticated; GRANT EXECUTE ... TO service_role;`. **Also audit `compute_driver_phase_distances` (migration 54) — same missing revoke (see below).** Add a CI guard: every new `SECURITY DEFINER` function must ship a matching REVOKE in the same migration.

### C3 — Two non-atomic background-timeout writes can clobber a live acceptance
`backend/routes/rides.py:2068–2093` (`ride_search_timeout`, `searching → cancelled`)
`backend/routes/rides.py:1356 → 1406–1417` (`driver_assigned → searching`, nulls `driver_id`)
Both handlers read the ride, check its status, then **write filtered by `id` only** with multiple `await`s in between. In batch dispatch a driver can accept inside that window; the timeout handler then overwrites `driver_accepted → cancelled` (violating "never cancel after accept") or reverts `driver_assigned → searching` and wipes `driver_id` (double-dispatch / lost acceptance).
**Why it matters:** These are the platform's core invariants — a rider's live ride silently dies, or two drivers get the same fare. The fix already exists in the same file.
**How to fix:** Atomic claim — `update_one("rides", {"id": r_id, "status": SEARCHING}, …)` / filter on `{"id", "status", "driver_id"}`; act on the WS/push fan-out **only when a row was returned**. `cancel_ride_rider:4661` is the reference pattern.

### C4 — Generic write helpers inherit the "read" retry policy → duplicate writes / double money movement
`backend/repositories/_base.py:173` (default `retry_policy="read"` = 3 attempts) applied to `insert_one`, `insert_many`, `update_one`, `rpc`, and the `wallet_repo` money RPCs (`wallet_transfer:146`, `increment_promo_uses:185`).
On a **post-commit** transport failure (server committed, response lost — `RemoteProtocolError`/`ReadTimeout`/`ConnectionTerminated`), a non-idempotent insert or non-idempotent RPC is retried and **duplicates**. Atomic conditional-update claims are safe; the exposure is the raw inserts and the two money RPCs.
**How to fix:** Default the write helpers to `retry_policy="write"` (no retry); opt genuinely-idempotent writes into an explicit `"idempotent_write"`. Make the wallet RPCs internally idempotent (idempotency key), matching the Stripe-event pattern already used elsewhere.

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

**Admin/telemetry side is strong; the user-facing side leaks in a handful of catch blocks.**

### T1 — Raw GPS coordinates written to application logs (PIPEDA violation)
`backend/routes/rides.py:1709,1737,1758,2527,2553,2577`; `backend/features.py:147,188`; `backend/routes/promotions.py:443` — 9 live sites interpolate raw pickup/dropoff lat+lng at 5-decimal (~1 m) precision into `logger.info`/`warning`. CLAUDE.md is explicit: **raw lat/lng may never appear in logs** (geohash area at most). These are `info`/`warning`, so the Sentry scrubber in `server.py` never sees them — they flow straight to log aggregation, exposing riders' precise home/destination to anyone with log access. **Fix:** drop the coordinates or emit a geohash prefix / service-area name.

### T2 — Raw `err.message` reaches the end-user (technical jargon leak)
- `driver-app/app/become-driver.tsx:270,495`, `driver-app/app/documents.tsx:257` — `Alert/showToast(..., err.message)` with **no fallback**; a timeout renders "Network request timed out", a non-Error throw renders "undefined".
- `rider-app/app/ride-options.tsx:437`, `rider-app/app/wallet.tsx:122`, `rider-app/store/walletStore.ts:86,126` — `error.message` leaks into the fallback chain / into rendered store state.
**Why it matters:** violates "no raw errors or technical jargon leak to the end-user." **Fix:** route through `extractError()`/`SpinrApiError.message` and always terminate the `||` chain with a friendly, non-technical fallback. (The many `err.response?.data?.detail || 'Friendly fallback'` sites are correct — backend `detail` is user-facing English — leave them.)

### T3 — Hardcoded no-show fee invented client-side
`rider-app/hooks/useRiderSocket.ts:147` — `A $${data.noshow_fee?.toFixed(2) ?? '4.50'} fee has been charged.` If the WS payload omits `noshow_fee`, the rider is told a specific dollar figure that may not match the actual charge. **Fix:** omit the amount when absent ("A no-show fee has been charged — see your receipt"); never invent a money value in the client.

### T4 — Loop watchdog blind to safety- and money-critical loops
`backend/core/lifespan.py:403–422` — `_WATCHDOG_LOOP_NAMES` omits `safety_checkin`, `preauth_capture`, `reconciliation`, `referral_payout`, `data_export_purge`, `suspension_reactivation`, `driver_claim_reaper`, `zoho_desk_sync`. Worse, `safety_checkin` records heartbeats as `"safety_checkin_loop"` while spawned as `"safety_checkin (30s)"` — a name mismatch that defeats the watchdog even if listed. A stalled safety check-in loop would never alert. **Fix:** reconcile the names and register the missing loops. Also `lifespan.py:302,335,346` log loop-import failures at `warning` — a safety loop failing to *start* should be `error` (→ Sentry).

**What's already excellent (keep):** every payment path uses `logger.error(exc_info=True)` + clean `HTTPException`; server-authoritative charge with underpaid-settlement block; Sentry loguru bridge; `send_default_pii=False`; phone/email/name/license consistently masked; refresh-token-reuse tripwire. This is above the bar for the stage.

---

## 🐢 Performance Bottlenecks & Optimizations

### P1 — Inline Stripe awaits block the single event loop
`backend/routes/payments.py` — `Customer.create` (134), `SetupIntent.create` (501, **745 with `confirm=True`** = live auth RTT), `PaymentMethod.list/attach/detach` (539/599/741/854), `Customer.modify` (758/811), `EphemeralKey.create` (921) all run inline. Each stalls **all** concurrent requests (dispatch, WS fan-out) for the full Stripe round-trip. The same file already wraps other calls in `asyncio.to_thread` (225–230, 412–414, 937–947) — **apply the identical wrap**. This is the single highest-leverage latency fix; it protects the <2 s dispatch SLA from Stripe latency spikes.

### P2 — Per-point Redis round-trips in the location hot path
`backend/routes/websocket.py:885–886` — the batch rate limiter calls `redis_incr` in a per-point loop: up to ~499 sequential awaited Redis RTTs per large GPS batch, blocking the receive loop. **Fix:** single `INCRBY`. Directly threatens the 150 ms location-write SLA under load.

### P3 — Inline FCM push on the trip-completion settlement path
`rides.py:5956–5965` awaits an FCM push inline on `rider_complete_ride` (settlement SLA <1 s), unlike sibling `spawn(...)` calls at 5732/5926. **Fix:** background it.

### P4 — Frontend: 15 s stall when a 401 races the proactive token refresh
`shared/api/client.ts` — `ensureFreshToken()` (163–186) and the reactive 401 handler (674–717) share `_refreshPromise`, but **only the reactive path flushes `_refreshSubscribers`**. When a request 401s while `ensureFreshToken` owns the in-flight refresh, it's parked and never woken — it hangs to the 15 s fetch timeout. This fires on the *hot path*: AppState-resume and pre-WS-connect. First action after backgrounding the app can freeze for 15 s then show a spurious timeout. **Fix:** `ensureFreshToken` must flush the subscriber queue on both success and failure.

**Already handled well:** dispatch matcher batches its reads (MGET / `.in_()`, no N+1); dispatch pushes are off the request path; estimate polyline overlaps fare work; `idx_drivers_dispatch_ready` partial index; explicit 32-worker DB thread pool; mobile location watcher throttles renders and caps buffers (500-point OOM guard).

---

## 💡 Tech Stack & Architecture Recommendations

**Current stack is sound and modern** (FastAPI, Supabase/Postgres+RLS, Redis pub/sub WS fan-out, Stripe, Expo SDK 54, Next.js 16). Against Uber/Lyft practice, the gaps are operational maturity, not framework choice:

1. **Backend image is bloated with runtime-irrelevant heavy SDKs.** `requirements.txt` ships `anthropic`, `openai`, `google-generativeai`, `pyiceberg`, `pandas`, `numpy`, `boto3` into a request-serving image. **Why it's a problem:** slower cold starts (matters on Fly/Railway scale-to-zero), a much larger dependency attack surface for a payments+PII service, and heavier SBOM/CVE triage. **Fix:** split ML/data/analytics deps into an optional extras group or a separate worker image; the API process should carry only what serves requests. Uber/Lyft keep request-path images minimal for exactly this reason.

2. **No staging environment — `main` deploys straight to prod (Fly + Railway).** This is the biggest single risk multiplier: it blocks safe migration rehearsal, load testing (the harness exists but can't run), and DAST. **Fix:** stand up a staging Fly app + throwaway Supabase with synthetic data (ACTION_ITEMS E1 — a prerequisite for E2/E4/E6).

3. **No external synthetic monitoring / SLO alerting.** A total outage is currently discovered by *users*. **Fix:** Checkly/Grafana-synthetic hitting `/health`, auth, and fare-estimate every minute from outside, alerting to PagerDuty, thresholds tied to the CLAUDE.md SLA table. Table stakes at Uber/Lyft.

4. **No kill switches / feature flags for risky subsystems.** `app_settings` covers config, but there's no way to disable the surge engine, scheduled dispatch, promo redemption, or corporate billing in seconds without a deploy. **Fix:** boolean flags checked at the top of each loop/path + admin toggles.

5. **No forced-upgrade gate for mobile.** Old binaries in the wild will hit changed APIs. **Fix:** `min_supported_version` in `app_settings`, a version header, a 426 response, and an "update required" screen. Cheap now, impossible to retrofit.

6. **DB access is a hand-rolled thread-pool over `supabase-py`.** Works and is carefully retry/circuit-breaker-wrapped, but a native async Postgres driver (`asyncpg`) for the hot dispatch/ride paths would remove thread-pool hops entirely. **Consider** for the latency-critical queries only; not urgent.

7. **Observability: distributed tracing is stubbed** (`X-Request-ID` propagation exists; no OpenTelemetry). Fine for now; revisit when multi-replica latency debugging gets painful (ACTION_ITEMS D2).

---

## 🛠️ Maintainability & Code Smells

- **Migration numbering has known duplicate prefixes** (08, 28, 29, 48, 50–58, 91, 92, 96, 138, 142, 143) — handled by full-filename idempotency keying and a CI prefix-uniqueness check, but it's a latent trap. The #166 retention regression (two PRs both `CREATE OR REPLACE`-ing `purge_pii_retention` from different forks) proves the failure mode is live. The cross-PR `CREATE OR REPLACE` target check (in flight) is the right structural fix — **land it.**
- **`any` on WS hot paths** — `useRiderSocket.ts:71/363`, `useDriverDashboard.ts:719` drive ride-lifecycle transitions off `data: any`; a renamed field is silently coerced. `shared/types/api/wsEvents.ts` exists — consume the discriminated union.
- **Bundled "Ride fare" receipt line** (`rides.py:506–509`, `fare_service.py:242–250`) collapses base + distance + time into one line while everything else is itemized. Per the "no hidden fee / every charge is a disclosed line item" guardrail, split into `base_fare` / `distance_fare` / `time_fare`. (Architect's call on strictness.)
- **Over-broad `except Exception` + `logger.warning`** at `rides.py:2091–2093` catches any DB fault, not just the intended missing-column PGRST204 — narrow it per the CLAUDE.md rule.
- **No `.github/CODEOWNERS`** — money/schema paths (`payments*`, `fare*`, `migrations/`) can merge on a drive-by approval. Add routing (ACTION_ITEMS E8).
- **Admin dashboard has no `global-error.tsx`** — a root-layout crash renders a blank page with no recovery. Add it.

---

## 🧪 Testing & QA (Missing Edge Cases)

- **Per-module money-coverage floors are not enforced.** CLAUDE.md mandates ≥90% for `payments.py`/`fare_service.py` and ≥80% for `rides.py`/`dispatch_service.py`, but `pytest.ini` sets a global 60% floor (ACTION_ITEMS A1 — "the single biggest remaining gap"). **This directly enabled C1** — a tax-recalculation branch shipped without a test. Ratchet per-path `--fail-under` in CI.
- **Missing regression tests for every finding above:** tax-on-recalc equality (C1), atomic-claim-under-concurrent-accept for both timeout races (C3), post-commit write-retry non-duplication (C4), and the 401-vs-proactive-refresh flush (P4).
- **Load/simulation harness exists but has never run** — `loadtest/locustfile.py` is built; execution is blocked on the missing staging env (E1→E2). Until it runs, the CLAUDE.md SLA table is aspirational, not measured.
- **No DAST / external pentest** before a payments+PII public launch (E6). SAST/Semgrep run in CI, but nothing exercises the *running* app.
- **Add a CI test** asserting no new `SECURITY DEFINER` function merges without a matching `REVOKE EXECUTE` (would have caught C2).

---

## 📈 Manager's Verdict — Overall Code Health

**Grade: B+ / strong pre-launch, not yet operationally mature.**

This is a disciplined, security-conscious codebase that has clearly absorbed many audit cycles. The hard problems — the JWT trust model, atomic dispatch claims, Stripe idempotency, RLS on financial tables, PII masking, refresh-token rotation — are *solved and correct*. The domain guardrails (0% commission, 2.5× surge cap, PIPEDA, SGI insurance periods) are encoded in the code, not just the docs. That is well above the median for a platform at this stage.

The remaining defects are few but sharp, and they cluster in the two areas that matter most for a money + safety product:

- **Money correctness:** C1 (stale tax) is a live, silent over/undercharge with a tax-compliance angle — **fix first.** C4 (write-retry duplication) is a latent double-charge/double-transfer. C2 (anon promo RPC) is real revenue/analytics abuse.
- **Concurrency invariants:** C3 (two timeout races) can kill a live ride or double-dispatch — the fix is already in the file, applied inconsistently.

Against Uber/Lyft, the **code** is closer than the **operation**. What's missing is the surrounding machinery a platform of that class treats as non-negotiable: a staging environment, external synthetic monitoring, kill switches, a forced-upgrade gate, an executed load test, and a pre-launch pentest. None are code-deep; all are launch-gating for a public payments platform.

### Recommended remediation order
1. **C1** — stale tax on recalculation (money + CRA). One function, add the test.
2. **C2** — REVOKE on `promo_increment_uses` (+ `compute_driver_phase_distances`); add the CI guard.
3. **C3** — atomic claims on both timeout races (copy `cancel_ride_rider:4661`).
4. **C4** — write helpers default to no-retry; idempotency keys on the two wallet RPCs.
5. **P1** — wrap the inline Stripe calls in `asyncio.to_thread`.
6. **T1** — strip raw GPS from the 9 log sites (PIPEDA — fast, high compliance value).
7. **P4 / T2 / T3** — frontend refresh-flush + error-message sanitization + no invented fees.
8. **Operational:** staging env → run the load test → synthetic monitoring → kill switches → forced-upgrade gate → pentest.

Items 1–4 are money/safety-critical and should gate the next deploy. Items 5–7 are fast follows. The operational track (8) runs in parallel and gates public launch.
