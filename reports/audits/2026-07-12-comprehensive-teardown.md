# Spinr — Comprehensive Engineering Teardown & Action Plan
**Date:** 2026-07-12 · **Reviewer:** Engineering Director / Chief Architect (read-only)
**Method:** Five parallel deep-dives — auth/security, payments, dispatch/real-time, frontend, testing/CI/infra — verified against actual code (file:line cited throughout). No code was modified.

**Bottom line up front:** Spinr is a genuinely well-engineered pre-launch platform — Decimal-only money math, atomic dispatch claims, refresh-token rotation with reuse-cascade, HttpOnly admin auth, and a documented bug-history discipline put it *ahead* of most startups at this stage. It is **not launch-ready**, and the blockers are concentrated in a small number of seams: a safety control that lies (driver SOS), a deploy pipeline with no test gate and disabled security scans, a class of dispatch timeout-vs-accept races, and three payment binding/idempotency gaps. Everything below is closable with targeted fixes, not rewrites.

---

## 🚨 Critical Issues & Security Flaws

| # | Finding | Location | Impact |
|---|---|---|---|
| C1 | **Driver SOS reports "Alert Sent" when the backend call failed** — `onTrigger` swallows the error in a `try/catch`, so `SOSButton` treats the rejected promise as success. Bypasses the component's own 3-attempt retry and 911-fallback machinery. | `driver-app/app/driver/(tabs)/index.tsx:892-894` | A driver in a real emergency sees a false "🚨 Emergency Alert Sent" while nothing was sent. This is the single worst finding in the audit. The rider app does this correctly (`rideStore.triggerEmergency` rethrows). |
| C2 | **Prod deploys have no test gate and deploy twice concurrently** — `deploy-backend.yml` (Railway) and `deploy-fly.yml` trigger on every `main` push with no `needs:` on any test job, racing `ci.yml`'s own gated deploy of the same service. | `.github/workflows/deploy-backend.yml`, `deploy-fly.yml`, `ci.yml:357-388` | A red test suite does not stop a production deploy; two Railway releases race for which wins. With no staging, `main → prod` is the only path. |
| C3 | **"Blocking" security gates G1–G4a are no-ops** — bandit, semgrep, eslint-security, and pip-audit all end in `\|\| true`, so they can never fail CI, while the summary job tells engineers they are "blocking." | `.github/workflows/security-gates.yml:45,82,105,122` | SAST/OWASP findings on a payments+PII platform are silently archived as artifacts. The team believes enforcement is on; it is off. |
| C4 | **PaymentIntent not bound to what it pays for** — `/payments/confirm` only rejects a PI whose `ride_id` *mismatches*; a wallet-top-up PI (no `ride_id`) sails through ownership + amount checks. | `backend/routes/payments.py:431-448` | A rider tops up $100 (webhook credits wallet), then confirms the same PI against a $50 ride → one charge, two credits. |
| C5 | **Three dispatch timeout-vs-accept races** — offer-timeout, batch-offer-expiry, and 5-min search-cancel all do check-then-act with an **unconditional** write, and fire exactly when accepts cluster at the countdown boundary. | `backend/routes/rides/matching.py:917-985, 1077-1159, 1188-1214` | An accepted ride reverts to `searching` (two drivers converge on one rider); the winning driver is released to `is_available=True` while holding the ride (double-booking); a **false insurance Period-1 row** is written to the append-only regulatory audit trail while the driver is actually in Period 2. State-machine + SGI-compliance violation. |
| C6 | **Render failover deploys the backend to a US region** — `if: failure()` auto-triggers a Render deploy in `oregon`, standing up rider PII + GPS + payment processing outside Canada. | `render.yaml:6`, `ci.yml:380-388` | PIPEDA data-residency breach path. CLAUDE.md calls a region change "a compliance event." Fly `yyz` is already the documented standby. |

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

**What's strong:** client-facing auth errors are static ("Invalid token") to prevent fingerprinting; PII scrubbing is real (`phone[-4:]`, SHA-256 digests, Sentry `before_send`, `send_default_pii=False`); the loguru→Sentry bridge delivers ERRORs; webhook idempotency `unclaim`s on no-side-effect failures.

**Where it leaks or lies:**
- **Raw backend jargon reaches end users.** Screens toast `error.message` directly, which for FastAPI validation is the joined `detail` (`"body.pickup_lat: field required"`) and for DB failures is `"Service temporarily unavailable: database"`. The client already extracts `messageKey`/`actionHint` on `SpinrApiError` for exactly this — the last mile (a `toUserMessage(err)` helper) was never wired. `rider-app/app/ride-options.tsx:493`, `wallet.tsx:128`, `manage-cards.tsx:106`, `profile-setup.tsx:76`.
- **Rides wedged into invisible states.** `/payments/confirm` writes raw Stripe status strings (`succeeded`, `requires_payment_method`) into `rides.payment_status`, and the `retrying`/`processing` claims can strand a ride on a hard crash — none of these are selected by any recovery scan, so the ride silently drops out of collection while the rider is told "already processed" or "paid." `payments.py:466-472`, `payment_retry.py:211-215,305-323`.
- **Regulatory GPS silently discarded.** Driver breadcrumb buffer (retained 3 yrs for SGI audit) is wiped with a `console.warn` after 3 failed uploads; a sustained backend outage mid-trip permanently loses the trace. `driver-app/hooks/useDriverDashboard.ts:530-539`.
- **Raw GPS in logs (PIPEDA).** `console.error(... {pickup_lat, pickup_lng, dropoff_lat, dropoff_lng})` logs raw coordinates; `redactCoords()` exists and is unused. `useDriverDashboard.ts:740-744`.
- **Admin `/session` and request-ID logging trust unsigned/unverified tokens** — `/session` reports `authenticated:true` without any revocation check (`admin/auth.py:249-297`); the log `user_id` is decoded with `verify_signature=False` and is client-forgeable (`core/middleware.py:213-227`).

---

## 🐢 Performance Bottlenecks & Optimizations

Ranked by threat to the CLAUDE.md SLAs (dispatch offer→notify P95 < 2s; WS fanout < 100ms; driver-location write < 150ms):

1. **Single serial WS pub/sub consumer with a 2s per-stuck-socket penalty** (`utils/ws_pubsub.py:324-414`, `socket_manager.py:296-334`). One half-closed client that receives ≥1 msg / 2s — i.e. any rider on an active trip getting 1 Hz location updates — makes the *whole replica's* consumer lag unboundedly; every offer/`ride_taken`/status change queues behind it for the ~30-50s until the heartbeat reaps the socket. **Fix:** decouple delivery from consumption (per-message `create_task` + bounded semaphore, or per-client queues that drop non-durable location messages when full).
2. **Dispatch hot path re-reads the same rows 4-6× serially** (`matching.py:192,320-328,409-458,718`). The `service_areas` row is fetched 3-5× and `driver_subscriptions` twice per attempt, and the no-driver retry repeats it every 10s per unfilled ride per replica. **Fix:** fetch area/parent once and thread through; merge the two subscription queries.
3. **Location-batch limiter does up to 500 sequential Redis INCRs per message** (`websocket.py:894-912`) — the "INCRBY isn't wrapped" comment is stale; `redis_incrby` exists. A 500-point recovery batch = ~500 round-trips blocking the socket receive loop. **Fix:** one `redis_incrby(key, len(points))`.
4. **N+1s:** driver-claim reaper runs ~400 queries/tick (`driver_claim_reaper.py:136-145`); scheduled dispatcher ticks every ~90s (lock never released) and dispatches serially inline (`scheduled_rides.py:257,285-303`). **Fix:** `.in_()` batching; release the lock at end-of-tick + bounded `asyncio.gather`.
5. **Unbounded Redis/in-process growth:** `spinr:ws:seq:{client}` keys never expire (`ws_pubsub.py:189-193`); the in-process Redis fallback dict never evicts unread expired keys (`redis_client.py:20-70`) — and prod silently enters this mode if `REDIS_URL` is unset.

---

## 💡 Tech Stack & Architecture Recommendations

The stack itself (FastAPI + Supabase + Redis + Stripe + Expo/RN + Next.js) is modern and appropriate for a 0%-commission regional platform; the gaps are in the *operational* tier, which is exactly where Uber/Lyft's decade of scar tissue lives.

- **Staging environment (the keystone gap).** Deploys go `main → prod` on Fly + Railway in parallel with no intermediate. This single missing piece blocks the load test (harness exists in `loadtest/`, can never run), safe migration rehearsal, and DAST. **Stand up a staging Fly app + throwaway Supabase with synthetic data — it unblocks four other items.**
- **Automated migration apply.** 231 migrations are applied *by hand*; no workflow runs the runner (and the runner path in CLAUDE.md is wrong — it's `backend/scripts/migrate.py`). Code deploys automatically, schema does not → guaranteed drift window on every schema-dependent deploy. **Add a gated migrate job (dry-run on PR, apply on main, before deploy).**
- **Migration-runner hardening.** `get_applied_versions` returns "assume none applied" on *any* DB read error (`scripts/migrate.py:150-160`) — a transient prod blip triggers a full 231-migration replay, re-running seed data; the `CONCURRENTLY` path splits SQL on bare `;`, corrupting any dollar-quoted function body. **Hard-fail on unreadable `schema_migrations`; use a real SQL parser (sqlparse/pglast).**
- **Per-module coverage enforcement.** CLAUDE.md mandates payments/fare ≥90%, rides/dispatch ≥80%; the only enforced floor is a global 60%, and the regression gate is `continue-on-error` + passes when the Codecov call fails. **Add a `coverage json` post-step asserting per-path floors as a blocking job.**
- **Synthetic monitoring + external DAST.** Nothing external probes the platform (a total outage is discovered by users); SAST runs but nothing exercises the running app. **Checkly/UptimeRobot against `/health`+auth+fare-estimate every minute → PagerDuty; scheduled OWASP ZAP baseline against staging; book one third-party pentest before public launch** (payments + PII warrants it).
- **Kill switches / feature flags.** `app_settings` covers config but there are no documented kill switches for the risky subsystems (surge, scheduled dispatch, promo, corporate billing). **Boolean flags checked at the top of each loop + admin toggles** so a misbehaving subsystem dies in seconds without a deploy.
- **Distributed WS delivery** (see Performance #1) is the one architectural change worth prioritizing — it's the load-bearing wall for the < 100ms fanout SLA under real concurrency.

---

## 🛠️ Maintainability & Code Smells

- **Duplicated, un-owned logic in the two riskiest places.** SOS retry is implemented in *two* layers (SOSButton 3× + `triggerEmergency` 3× = up to 9 sequential attempts / >1 min before the 911 prompt on a dead network — `SOSButton.tsx:28-29,129-138`, `rideStore.ts:843-858`). Wallet/Stripe customer creation is duplicated with *divergent PIPEDA posture* (`wallet.py:169-176` sends full name+email; `payments.py:130-138` is metadata-only). Corporate settlement debits are duplicated outside the idempotent RPC contract. **Consolidate each into one owned implementation.**
- **Dead code that consumers still believe in.** `shared/api/offlineQueue.ts` is imported by nothing; `rideStore.syncOfflineRequests` replays an `offline_queue` key nothing writes — and *would* replay `create_ride` with no idempotency key or staleness check (a days-old queued booking would charge the rider). `locationStore.initialize` uses `navigator.geolocation` (undefined on RN) so it always throws on native, yet `report-safety.tsx` reads its store and stamps a fresh timestamp on rehydrated days-old coordinates. **Delete or properly wire — this is latent-landmine territory.**
- **Types written then never adopted.** `shared/types/api/wsEvents.ts` has zero importers; every WS handler on the dispatch-critical path is `any` (`data.fare || 0` fails silently to $0). ~31 `any` hits across the two socket hooks.
- **Test fixtures encode illegal states.** `conftest.py sample_driver_data` sets `is_available:True, is_online:False` — the exact invariant CLAUDE.md forbids — so an invariant-enforcing change would break tests "mysteriously." The universal chainable `mock_supabase_client` returns `data=[]` for every query (tests pass regardless of what the code asks for), and `mock_db_collections` mocks a MongoDB-era API no production code uses.
- **Stray root-level `test_*.py`** (`backend/test_corporate_accounts.py` hits the real DB with `.env` creds if collected via `pytest backend/`).

---

## 🧪 Testing & QA (Missing Edge Cases)

**Pyramid shape is inverted:** ~3,691 mocked unit tests, **1** real-DB integration test, 21 mocked "e2e" files (in-process, not true E2E), 3 Playwright specs (admin only, advisory on PRs), 1 load harness that can never run. Rider/driver apps have zero E2E in CI.

Highest-value missing cases, mapped to the findings above:
- **Timeout-vs-accept race** (C5): no test exercises a driver accept landing inside the offer-timeout / batch-expiry / search-cancel window. Add cases that assert the conditional-write pattern rejects the stale timeout.
- **PI binding** (C4): no test asserts a wallet-top-up PI is rejected by `/payments/confirm`.
- **Tip-after-settlement** (H3 below): no guard/test that a tip on an already-`paid` ride is rejected or routed to an ancillary charge — currently pure uncollected driver credit; `/rate` even allows unlimited tip accumulation (no `ERR_TIP_DUPLICATE`). `rating.py:64-87`, `rides/payments.py:52-113`.
- **Corporate settlement replay** (H2): no test for double-debit when the process crashes between allowance/master-wallet debit and `update_ride(paid)` — the RPC dedups on `stripe_payment_intent_id`, which is NULL for corporate rides.
- **`/wallet/pay` on a taxed ride** (M1): the route bounds on `total_fare` while the RPC enforces `grand_total`, so *any* ride with GST is currently unpayable via wallet — an integration test would have caught a dead endpoint.
- **Partial refund** (M6): `charge.refunded` marks a ride fully `refunded` on a $1 partial refund, blocking later settlement. No test distinguishes `partially_refunded`.

**Process fixes:** flip Playwright to blocking on PRs once flake rate is measured; add a real ride-lifecycle integration test against a throwaway schema; make the tier markers real (`slow` is used by 0 tests, `unit` by ~28/310 files, so `pytest -m "not slow"` deselects nothing).

---

## 📈 Manager's Verdict

**Overall code health: B+ / "strong engineering, weak operations — not yet launch-ready."**

This is a codebase built by people who clearly know what they're doing. The money layer is Decimal-clean with commented guards for every historical bug class; dispatch uses atomic claims and a sweeper/reaper safety net; auth has token-version revocation, refresh-rotation with reuse-cascade, and PII-conscious logging; the admin HttpOnly migration is complete and clean. Every reviewer independently noted that findings were *residual gaps, not systemic design flaws* — the codebase already contains the correct pattern for nearly every bug found, applied everywhere except the one place that drifted.

**Versus Uber/Lyft:** functionally, dispatch/pricing/insurance-period modeling is at parity for a regional operator, and the 0%-commission + SK-regulatory design is a deliberate, defensible differentiation. The gap is the operational maturity those platforms paid for in outages: no staging, manual migrations, disabled security gates, ungated deploys, no external monitoring, no load validation, no kill switches. Uber/Lyft's reliability isn't better code — it's the pipeline *around* the code, and that's exactly Spinr's soft underbelly.

**Launch gate:** I would **block launch** on C1 (SOS lies), C2+C3 (deploy has no test gate, security scans disabled), C4+H1-class payment binding, C5 (dispatch races corrupt the insurance audit trail), and C6 (US-region failover). None require a rewrite; all are days-not-weeks fixes. The single highest-leverage move is **standing up a staging environment** — it unblocks load testing, migration rehearsal, and DAST simultaneously.

---

## Prioritized Action Plan

### P0 — Block launch (days)
1. **Fix driver SOS** — make `onTrigger` rethrow; move the trigger into `shared/` so both apps use one audited path. Add a GPS-fix timeout (`Promise.race` ~3-5s) and collapse the double retry to one layer, capped ~10s before the 911 prompt. *(C1, plus SOSButton timeout/retry findings)*
2. **Gate deploys on tests** — collapse to one pipeline: Railway+Fly deploys as jobs inside `ci.yml` gated on `backend-test`; delete the duplicate ungated Railway path. *(C2)*
3. **Un-disable security gates** — remove `\|\| true` from G1/G3/G4a. *(C3)*
4. **Bind PaymentIntents** — require `intent.metadata.ride_id == ride_id`; hard-reject `scope=wallet_topup/corporate_topup` PIs on `/payments/confirm`; include stored tip in the owed calc. *(C4, H3)*
5. **Close the dispatch race class** — add status+driver filters to the three unconditional writes so a stale timeout that matches zero rows skips all side effects (driver release, WS, re-dispatch, period write). *(C5)*
6. **Remove the US-region failover** — point Render to a Canadian region or delete the Render fallback. *(C6)*

### P1 — Fix before launch (1-2 weeks)
7. Corporate settlement idempotency — give `corporate_wallet_apply_delta` a ride-scoped dedup key (`ride-debit-{ride_id}`); extend the retry redrive to corporate rides. *(H2, M4)*
8. Admin auth hardening — Redis-back the admin login limiter (currently per-worker memory) + key on real client IP; give `admin-001` a revocation anchor; route `/session` through `_verify_admin_payload`. *(H1, H2-auth, M1-auth)*
9. Map `/payments/confirm` status writes to contract values; release the `processing`/`retrying` claims on crash so rides stay recoverable. *(M2, M3, M7)*
10. `/wallet/pay` validate against `grand_total`; `charge.refunded` distinguish partial vs full. *(M1, M6-pay)*
11. Wire `toUserMessage(err)` at all toast sites; stop discarding regulatory breadcrumbs; use `redactCoords()` in the GPS log. *(telemetry group)*
12. Stand up **staging** (unblocks load test + migration rehearsal + DAST); add the **automated migration-apply job** and harden the runner.
13. WS delivery decoupling (Performance #1) and dispatch hot-path read consolidation (#2).

### P2 — Operational hardening (post-launch, tracked)
14. Per-module coverage enforcement; make test tier markers real; delete dead offline-queue / locationStore code; adopt `wsEvents` types.
15. External synthetic monitoring → PagerDuty; scheduled DAST; book third-party pentest; kill switches for risky subsystems; fix `conftest` invariant-violating fixture.
