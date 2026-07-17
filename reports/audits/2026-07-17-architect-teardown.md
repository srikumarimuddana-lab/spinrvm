# Spinr — Architect-Level Code Teardown & Remediation Plan

**Date:** 2026-07-17
**Reviewer:** Engineering Director / Chief Architect pass (read-only)
**Branch reviewed:** `claude/epic-planck-llkwy7` (working tree)
**Method:** 5 parallel specialist reviews (security, money/fare, dispatch/perf, client/error-UX, testing/CI) grounded against the actual code + an independent architecture read. All findings carry `file:line`.
**Benchmark:** Uber / Lyft engineering-org practice at equivalent stage.

---

## 0. Executive summary (read this first)

Spinr is **not** an early-stage codebase pretending to be mature — it is a genuinely well-hardened platform whose security, money-arithmetic, and dispatch-correctness cores are *materially above average* for a pre-launch product. The JWT trust model, refresh-token reuse detection, OTP handling, Stripe webhook idempotency, RLS enumeration, Decimal-only money math, and the ride state machine are all staff-level and show clear scar tissue from prior incidents. The `ACTION_ITEMS.md` / sprint backlog is unusually disciplined.

That said, an independent pass **still surfaced a cluster of real, un-tracked defects** — concentrated not in the well-worn cores but in the *seams*: re-dispatch overlap, dispute→refund messaging, offline resilience, emergency-path retry stacking, and receipt/ surge transparency. None are secret-leak or money-loss blockers, but several are user-trust and KPI risks. The single biggest *systemic* gap is that the codebase's own quality contracts (per-module coverage floors, "every Stripe webhook tested", SAST-blocks-merge) are **documented but not enforced by CI** — they are honor-system, and an honor-system contract on a payments platform is a latent regression surface.

**Health score: 8.2 / 10.** The delta from a 9+ is (a) the seam bugs below and (b) closing the gap between the stated engineering bar and what CI actually enforces.

---

## 🚨 Critical Issues & Security Flaws

Security-specific audit returned **zero merge blockers** — no committed secrets, no CORS wildcard in prod, no unauthenticated admin routes, no `FOR ALL` RLS, no float fare math, no unguarded webhook. What follows are correctness/trust defects with the highest blast radius, ranked.

| # | Severity | Location | Defect & why it matters |
|---|----------|----------|--------------------------|
| C1 | **High** | `backend/routes/rides/matching.py:1167` `_batch_offer_timeout_handler` | The batch timeout handler expires **all** `status='pending'` offers for the ride, not the batch it created (no `offered_at`/batch-token scope). Because the ride stays `searching` (batch path never flips to `driver_assigned`), a stale batch-A timer wakes at t15 and yanks batch-B's still-valid offers 7s early — driver's countdown lied. Cascades re-dispatch, spams `ride_offer_expired`, degrades **match-rate KPI (≥85%)** and the **<2 s dispatch SLA**. `process_expired_offer`'s atomic claim prevents *double*-processing but does not check the offer's own `expires_at`. |
| C2 | **High** | `matching.py:176` guard + callers at `matching.py:84`, `ride_flow.py:541`, `matching.py:1217` | **No per-ride dispatch lock.** With the ride in `searching` during outstanding offers, the `status != SEARCHING → return` guard doesn't stop re-entrant dispatch. Three call sites (`_dispatch_retry`, `decline_ride` re-dispatch, batch-timeout re-dispatch) can overlap, each claiming a disjoint driver set → over-offering beyond `max_offers`, transient supply erosion, double-notify. |
| C3 | **High (trust)** | `backend/routes/disputes.py:293-298` | The "A refund of $X has been issued" push fires whenever `resolution in ("approved","partial_refund")` **regardless of `refund_result.status == "manual_required"`**. When a dispute has no `payment_intent_id` (cash/unprocessed), no Stripe refund happens — yet the rider is told their money was refunded. Direct customer-trust and support-load hit. (Same block logs at `warning` where a payment anomaly warrants `error` per house rules.) |
| C4 | **High (safety UX)** | `shared/components/SOSButton.tsx:28,129-138` + `rideStore.ts:853-868` | **Nested SOS retry loops.** `SOSButton` retries 3× `[1s,2s]`; the `triggerEmergency` it calls *also* retries 3× `[1s,2s]`, each with a 15 s client timeout → **up to 9 POSTs**. On the flaky link typical of a real emergency, the "Alert Not Sent → Call 911" dialog can take 1–2 min to appear, and the button is `disabled` with no visible 911 affordance while sending. This is the one finding I'd fix *before* any real rider is on the platform. |
| C5 | **Med (compliance landmine)** | `backend/utils/receipt_email.py:18-19,55-56` | Dead-but-wired: hardcodes `_PST_RATE = 0.06` applied unconditionally, contradicting the real engine (`features.py:924-947`: "SK rideshare is GST 5% only; PST does NOT apply"). Currently referenced only by tests, but if ever wired it charges a fictitious 6% PST — a CRA remittance + reconciliation problem. The live path (`email_receipt.py`) is correct. |
| C6 | Info | `ACTION_ITEMS.md:56-63` (B2) | **Stale backlog:** the disputes RLS + refund-rounding item is listed open but is *already fixed* — `migration 142` enumerated the disputes policy + `disputes.py:219` now uses `dollars_to_cents()`. Risk: a future session re-does closed work or assumes it's still broken. |

**Genuinely strong (calibration):** JWT role always re-read from DB for rider/driver (`dependencies/__init__.py:490`); admin tokens require `aud`+live `is_active`+`token_version`+30-min idle check on every request; refresh-token OAuth2-BCP reuse-cascade detection with 60 s grace; OTP SHA-256 + `hmac.compare_digest` + 5-fail lockout + fail-closed dev bypass; webhook signature-before-payload + `claim_stripe_event` first; Supabase region must start with `ca-` (PIPEDA residency, fails closed at boot).

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**User-facing side — mostly excellent, two leaks:**
- `shared/api/client.ts:357-464` `extractError`/`getApiErrorMessage` is staff-level: handles all 4 backend error shapes, strips Axios's "Request failed with status code N"/"Network Error", clamps toast length. The 401 refresh interceptor (`:762-806`) does single-flight dedup, a subscriber queue, ref-counted sign-out suppression, and an explicit **SOS exemption** so a token lapse can't sign you out mid-emergency.
- **T1 (Med):** `shared/components/ErrorBoundary.tsx:40,47-55` renders `error.name: error.message` + full JS/component stack to end users **deliberately not gated behind `__DEV__`**. A render crash shows a rider a stack trace (technical leak, possible PII in the component stack). → gate diagnostics behind `__DEV__`.
- **T2 (Low, out-of-scope surface):** legacy `frontend/` still `Alert`s raw `error.message` (`payment-confirm.tsx:37`, `login.tsx:63`, …). The in-scope rider/driver apps do not. If `frontend/` still ships, real leaks; otherwise delete it.

**Admin/telemetry side — production-grade:**
- Sentry wired with loguru→Sentry bridge; `send_default_pii=False`; Prometheus `/metrics` token-authed via `METRICS_AUTH_TOKEN`.
- PIPEDA log discipline is real, not comment-deep: SMS-failure paths avoid `str(exception)` because "Twilio errors embed the destination number" (`safety.py:143`); admin lockout logs hash email to `email_sha256:`; `_redactGpsUrl` strips lat/lng from logged URLs client-side.
- **T3 (Med, house-rule):** `disputes.py:224,293` uses `logger.warning` + continue on a *payment*-path anomaly where the CLAUDE.md rule mandates `logger.error(exc_info=True)`. Same class of silent-softening the repo explicitly forbids.

---

## 🐢 Performance Bottlenecks & Optimizations

The dispatch hot path is already carefully engineered (batched `$in` skip-key/subscription/quota lookups, geo bounding-box pre-filter bounding a `LIMIT 500` scan, single `batch_get_etas` under a 1.2 s timeout, pushes `spawn`-ed never awaited). The remaining bottlenecks are seams:

| # | Sev | Location | Bottleneck → fix |
|---|-----|----------|-------------------|
| P1 | Med | `ws_pubsub.py:_consumer:324` → `socket_manager.py:296` | **Unicast WS fan-out is serial.** One half-open socket blocks the replica's *entire* unicast stream up to 2 s/msg (head-of-line), breaching the <100 ms fan-out SLA. Broadcast path already uses `asyncio.gather`; unicast doesn't. → dispatch `_deliver_local` via `create_task`/bounded pool. |
| P2 | Med | `dispatch_service.py:270`, `matching.py:343,439,473` | Same `service_areas` row re-fetched **3–4× per dispatch attempt** (serial round-trips on the <2 s clock, every attempt, every retry, every replica). → fetch once at attempt top, thread through. |
| P3 | Med | `websocket.py:978` vs `:779` | `location_batch` path issues an **uncached** `rides` query per batch, while the single-ping path is Redis-cache-served (`resolve_active_rides_cached`, 5 s TTL). Offline-recovery bursts hammer the DB. → reuse the cache in the batch branch. |
| P4 | Med | `socket_manager.py:51` (ACTION_ITEMS B4) | WS per-user rate limit is an **in-process dict → per-replica**; attacker who force-balances sockets gets `replica_count × 30 msg/s`. Ironically the `location_batch` limiter next door already uses Redis `INCR`/`EXPIRE`. → promote to the same Redis window. |
| P5 | Low | `websocket.py:845,1017` | `refresh_ride_eta` has no single-flight guard; a cold-cache 30/s burst spawns N concurrent (metered) Maps calls. → Redis `SET NX` "refresh in progress" per `driver:ride`. |
| P6 | Low | `routes/admin/analytics.py:72` (ACTION_ITEMS D7) | Cancellation-breakdown recomputed per dashboard load; 5-min Redis cache drops DB load ~98%. |

---

## 💡 Tech Stack & Architecture Recommendations (vs. Uber / Lyft)

**The stack itself is right-sized and modern** — FastAPI + Supabase(Postgres+RLS) + Redis + Stripe + FCM, single horizontally-scalable process, Fly-primary/Railway-standby with a Cloudflare-CNAME DNS failover. For a Saskatchewan-first 0%-commission product, adopting Uber's cell-based multi-region microservice topology would be *over-engineering*. The gaps are operational-maturity gaps, not stack-choice gaps.

Where Uber/Lyft-grade orgs have muscle Spinr does not yet:

1. **No staging environment (E1).** Deploys go `main` → prod (Fly + Railway) with nothing in between. This is the *root* blocker: it's why the built load-test harness (`loadtest/locustfile.py`, E2) has never run, why migrations can't be rehearsed, and why there's no safe DAST target. **Highest-leverage single investment** — stand up a staging Fly app + throwaway Supabase project. Uber/Lyft would never ship schema to prod unrehearsed.
2. **No global kill switches / feature flags (E5).** Only one narrow per-area Spinr-Pass switch exists. A misbehaving surge engine, scheduled-dispatch loop, promo redemption, or corporate-billing path currently needs a *deploy* to stop. Every mature marketplace has second-scale runtime kill switches. → boolean `app_settings` flags checked at the top of each of the 16 loops + risky paths, with admin toggles.
3. **No forced-upgrade / min-version gate (E3).** No `min_supported_version` anywhere. Old app binaries will silently break on API drift — and this is *impossible to retrofit* onto clients already in the wild. Both Uber and Lyft hard-gate this. → version header + 426 response + "update required" screen. Cheap now.
4. **No external synthetic monitoring (E4).** Nothing outside the platform probes it; a total outage is discovered by users. → Checkly/Grafana synthetic hitting `/health`, auth, fare-estimate every minute → PagerDuty, thresholds tied to the CLAUDE.md SLA table.
5. **Manual-surge-override feature is inert (money finding #4):** every surge-read path does `min(area_surge, SURGE_CAP)` regardless of `surge_source`, so an admin override >2.5× (with mandatory written justification) is silently clamped to 2.5× before any rider sees it. The whole justification workflow is defanged. → skip the clamp when `surge_source == "manual"` (the write-time justification gate is the intended control).
6. **Receipt transparency gap (money finding #2):** in-app fare breakdown splits surge into an explicit dollar line (`fare_service.py:246`), but the **emailed** receipt (`email_receipt.py:130`) still uses the old amount-less "Surge X× was in effect" copy — violating the "no hidden fee, every charge maps to a disclosed line item" product law. → render an explicit surge $ line in the email.

**Architecture doc drift (Maintainability-adjacent):** `ARCHITECTURE.md` still says Railway-only hosting + Expo SDK 54 + a flat route layout; reality is Fly-primary/Railway-standby, SDK 55, and a nested `routes/rides/*`, `routes/admin/*` structure. `graphify-out/` is referenced by `CLAUDE.md` (mandatory pre-read) but **does not exist** in the tree. These mislead every new contributor and agent.

---

## 🛠️ Maintainability & Code Smells

- **Two HTTP clients, one weaker (M1, Med):** `shared/api/cachedClient.ts` reads the token from SecureStore only, but the access token is memory-only (`authStore.setTokens`) → its `Authorization` header is frequently null → spurious 401s. It has no 401-refresh, no typed errors, and uses `localStorage` on web where the main client uses `sessionStorage` (divergent XSS surface). → route through `client.ts`.
- **Dead offline-resilience layer (M2 — see Testing/UX):** `shared/api/offlineQueue.ts` has **zero callers**; `rideStore.syncOfflineRequests` *drains* an `offline_queue` key nothing ever *writes*. Either wire it or delete it — dead safety-adjacent code is worse than none.
- **Migration debt:** 291 migration files, latest slot 232, with documented duplicate prefixes at 08/28/29/48/50/51/52/54/55/56/57/58/91/92/96/138/142/143 and beyond. Full-filename keying makes them *apply* correctly and a CI prefix-uniqueness check now blocks new ones, but the sheer count + the #166 slot-collision incident (retention silently broken 45 min) show the numeric-slot scheme is near its scaling limit. Consider timestamp-based names for new migrations.
- **Money-code convention drift (low, latent):** `corporate_autotopup.py:123,129` uses `int(round(x*100))` (ROUND_HALF_EVEN) instead of the mandated `dollars_to_cents()` (HALF_UP); `booking.py:827` does a `Decimal→float→str→Decimal` roundtrip; `fare_service.py:294` uses raw `float()`. All numerically inert *today* because inputs are pre-quantized, but each is a bad pattern waiting to be copied onto un-quantized input.
- **`as any` residue:** shared types/stores are clean (the sweep landed), but rider-app **screens** still carry ~165 casts, some hiding genuinely-missing interface fields (`Ride.promo_error`, `planned_route_polyline`) and some pointlessly casting to reach fields that already exist (`grand_total`). → add the missing fields, drop the casts.
- **Admin-router docstring drift:** `routes/admin/__init__.py:32-35` claims `monitoring.py` mounts outside `admin_router`; it actually mounts inside (double-gated, so no auth gap, but a refactor trap).

---

## 🧪 Testing & QA (missing edge cases)

**Strong and worth protecting:** `test_surge_engine.py` pins every tier edge + `SURGE_CAP==2.5`; `test_ride_state_machine.py` covers double-cancel, cancel-after-`in_progress` (409), idempotent complete; corporate/promo/refund each have dedicated suites incl. partial-refund tax proration; offer-timeout/reaper and driver-cancel atomicity are covered.

**The systemic gap (Critical):** the quality *contracts* are documented but **not enforced by CI.**
- `pytest.ini:15` sets a single global `--cov-fail-under=60`; the per-module floors (payments/fare ≥90%, rides/dispatch ≥80%) exist **only in CLAUDE.md prose**. `ci-guardrails.yml`'s coverage gate is `continue-on-error: true` (advisory) and whole-repo only. → add a *blocking* `coverage report --include=... --fail-under=90/80` step. **This is ACTION_ITEMS A1 and I confirm it's the single biggest gap.**
- **12 handled Stripe webhook types have zero tests** — including `charge.dispute.created/closed` (chargebacks, the highest financial risk), `charge.captured`, `payment_intent.canceled/processing/requires_action`, `invoice.finalized`, `payout.created`. CLAUDE.md mandates "every Stripe webhook type before production"; tests cover happy-path only. → parametrized dispute/chargeback + capture-lifecycle test class.
- **SAST is non-blocking:** `security-gates.yml` Bandit (`:45`) and Semgrep (`:105`) both end with `|| true` — findings upload as SARIF but never fail the merge. → drop `|| true`.

**Untested edge cases (Med):** webhook *out-of-order* delivery (idempotency handles replay, not ordering); refund-after-*partial-capture* (over-refund guard untested); chargeback/dispute lifecycle end-to-end; the offline-replay double-book path (idempotency key generated at send time, not persisted with the queued request — `rideStore.ts:571,682`).

**a11y (Med):** WCAG 2.1 AA is a *stated regulatory mandate* but nothing runs axe in CI, and the **driver-app is badly under it** — ~150 `TouchableOpacity`, only 9 `accessibilityLabel` (rider-app: 69). Icon-only controls unlabeled. → add labels + `eslint-plugin-react-native-a11y` + wire axe into the Playwright E2E.

**Missing CI muscle vs. Uber/Lyft:** no `.github/CODEOWNERS` (money/migration changes merge on drive-by approval — E8); post-deploy smoke is `/health`-only *and* conditional on a secret being set (A2); no DAST/ZAP (E6); load-test harness built but unexecuted (E2); no license scan on the RN/JS surfaces (Python-only, E10).

---

## 📈 Manager's Verdict — overall code health

**Grade: B+ / 8.2 of 10. Cleared for launch conditional on the P0 list below.**

This is a codebase built by people who have clearly been burned and learned. The cores that matter most on a payments-and-safety platform — auth, money arithmetic, webhook idempotency, RLS, the ride state machine — are correct, tested, and defensively written. Documentation and backlog discipline are top-decile. If I were doing diligence on this as an acquirer, the *engineering* would not scare me.

What separates it from a launch-ready 9+ is a consistent pattern: **the seams are softer than the cores, and the enforcement is softer than the intent.** The novel defects this pass found all live between well-built components (re-dispatch overlap, dispute→refund messaging, nested SOS retries, dead offline queue, receipt/surge transparency), and the biggest systemic risk is that the team's own excellent standards are honor-system in CI rather than gates. On a 0%-commission consumer marketplace, trust *is* the product — the refund-that-wasn't push (C3) and the surge-not-shown-on-receipt gap do more brand damage than any latency number.

**Readiness by domain:** Security **A**, Money-core **A-**, Dispatch-core **A-**, Observability **A-**, Client error-UX **B+**, Testing-enforcement **C+**, Ops-maturity (staging/flags/monitoring) **C**.

---

## Prioritized remediation plan

### P0 — before a real rider is on the platform (this week)
1. **C4 — de-stack SOS retries** + always-visible "Call 911" while sending + SOS-specific short timeout. *(safety, 1 file group)*
2. **C3 — gate the "refund issued" push** on `refund_result.status not in (None,"manual_required")` + promote log to `error`. *(trust, `disputes.py`)*
3. **C1 — scope the batch offer-timeout handler** to its own batch (or gate `process_expired_offer` on `expires_at <= now`). *(match-rate KPI, `matching.py`)*
4. **C2 — per-ride dispatch lock** (Redis `SET NX spinr:dispatch:{ride_id}`) around a dispatch attempt. *(supply integrity, `matching.py`)*

### P1 — before public launch (this sprint)
5. **A1 — enforce per-module coverage floors in CI** (blocking `--fail-under` for payments/fare 90, rides/dispatch 80).
6. **Test chargeback + the 11 other handled webhook types**; add out-of-order + partial-capture-refund cases.
7. **Drop `|| true` on Bandit/Semgrep**; add `.github/CODEOWNERS` for `payments*`/`fare*`/`migrations/`.
8. **T1 — gate `ErrorBoundary` diagnostics behind `__DEV__`.**
9. **Money transparency:** explicit surge $ line in email receipt; **delete or repoint `receipt_email.py`** (C5).
10. **Decide + fix the manual-surge-override clamp** (money #4) — either honor `surge_source=="manual"` or remove the dead admin workflow.
11. **Wire or delete the offline queue** (M2); if wired, persist the idempotency key with the queued request.

### P2 — operational maturity (next sprint, unblocks the rest)
12. **Stand up staging (E1)** → then execute the load-test harness (E2) and add a real post-deploy smoke (A2).
13. **Global kill switches (E5)** for surge / scheduled-dispatch / promo / corporate.
14. **Forced-upgrade / min-version gate (E3).**
15. **External synthetic monitoring (E4)** → PagerDuty, thresholds from the SLA table.
16. **Perf seams:** async unicast WS fan-out (P1), single-fetch `service_areas` per dispatch (P2), cache the batch-location rides read (P3), Redis WS rate-limit (P4).

### P3 — hygiene / debt
17. Refresh `ARCHITECTURE.md` (Fly-primary, SDK 55, nested routes); restore or remove the `graphify-out/` reference in `CLAUDE.md`.
18. Route `cachedClient.ts` through `client.ts`; driver-app a11y labels + axe in CI.
19. Money-convention tidy (`corporate_autotopup.py`, `booking.py:827`, `fare_service.py:294`) to `dollars_to_cents()`/Decimal.
20. Update `ACTION_ITEMS.md` B2 → done (migration 142); consider timestamp-based migration naming going forward.
