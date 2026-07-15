# Spinr — Comprehensive Architecture & Code Teardown

**Date:** 2026-07-15
**Reviewer:** Engineering Director / Chief Architect review pass (read-only)
**Scope:** Backend (dispatch, rides, payments, auth, background loops, WS), shared mobile client, admin surface.
**Method:** Four parallel deep-read audits (dispatch/rides/WS, payments/fare/wallet, auth/security/PII, reliability/loops) plus a direct read of the shared API client and offline queue. Findings are code-verified where marked **CONFIRMED**; **PLAUSIBLE** items need an owner/runtime check.

> This is an analytical teardown and remediation plan. **No production code was modified.** Line numbers were accurate at the reviewed commit (`541db0f`) and should be re-anchored before fixing.

---

## Executive framing vs. the market (Uber / Lyft)

Spinr is architecturally in the same weight class as an early Uber/Lyft: FastAPI + Supabase(Postgres+RLS) + Redis + Stripe, Expo/React Native clients, Next.js 16/React 19 admin. The engineering *discipline* here is unusually strong for the stage — a documented state machine, insurance-period accounting, replay-safe loop contract, Decimal-only money rules, refresh-token rotation with reuse detection, RLS-first migrations, and an extensive prior-audit trail. That is ahead of where most pre-launch platforms sit.

Where Spinr still trails the incumbents is **not** feature breadth — it is the *operational safety net* around a correct-looking codebase: no staging environment, unexercised failover/restore runbooks, no external synthetic monitoring, no feature kill-switches, and per-domain money-path coverage floors that aren't yet enforced. Uber/Lyft's real moat at this layer is that a bad deploy or a silently-wedged background loop is caught by machines in minutes, not by users. The findings below cluster in exactly that gap: several bugs are **silent** — the system reports healthy while under-collecting tax, halting payment retries, or misclassifying insurance periods.

The single most important takeaway: **the highest-severity issues are all "looks healthy, is quietly wrong" failures.** They will not surface in a happy-path demo. They surface in production, at scale, in the money and safety paths.

---

## 🚨 Critical Issues & Security Flaws

### C1 — Account takeover via corporate email-OTP brute force · CONFIRMED · **launch-blocking**
**`backend/routes/auth.py:637` (`verify-email-otp`), `:589` (`send-email-otp`)**

The phone OTP path is hardened: `_check_otp_lockout` + `_record_otp_failure` (5 fails/hr → 24h Redis lockout, fail-closed). The **corporate email OTP path has none of it** — only `@limiter.limit("5/minute")` keyed on IP. On a wrong code it raises `ERR_OTP_INVALID` with no failure counter, and it **does not invalidate the OTP row** on failure, so the same code can be guessed until it expires.

- OTP is **4 digits** (`dependencies/__init__.py:51` `OTP_LENGTH = 4` → 10,000 combinations), valid **5 minutes** (`OTP_EXPIRY_MINUTES = 5`).
- `send-email-otp` is unauthenticated — an attacker mints a code for any victim email.
- The portal mount `/api/portal` is in **both** the App-Check-exempt prefixes (`middleware.py:112`) and the CSRF-exempt-exact set (`middleware.py:43`) — so only 5/min/IP stands in front of the code. Across a modest proxy pool the 10k space is coverable inside the 5-minute window.
- On success, `_issue_company_email_session` (`:485`) mints a **full rider session** for *whatever* account `_find_user_by_email` returns — including **driver** accounts (drivers set `email` at profile creation).

**Why it matters:** Direct account takeover of riders and drivers on a payments + PII platform. This is the same brute-force exposure the phone flow was explicitly hardened against (SEC-008); the later corporate flow never inherited the hardening.

**How to fix:** Port `_check_otp_lockout` / `_record_otp_failure` (fail-closed on Redis) to the email verify path; bind attempts to the specific OTP row and invalidate it after N failures; add a per-email send cap mirroring `_enforce_otp_send_cap`; restrict `_issue_company_email_session` to accounts that are actually corporate members / invited emails (finding C2); consider a 6-digit email code (no SMS-cost pressure there).

### C2 — Email-OTP logs into *any* account matching the email · CONFIRMED (enabler for C1)
**`backend/routes/auth.py:485` / `:467`**

`_find_user_by_email` is a bare `users` lookup with no corporate-domain or `corporate_members` invitee check, so the weaker email channel can authenticate essentially every profile-complete account (consumer riders *and* drivers), amplifying C1 from "corporate portal" to "any account."

**Fix:** Gate email-OTP session issuance to invited/corporate-member emails or accounts created via the email channel.

### C3 — `/wallet/pay` structurally under-collects GST/PST + area fees (tax non-remittance) · CONFIRMED
**`backend/routes/wallet.py:249-254`**

The endpoint validates the debit against `total_fare` (fare-side subtotal) with a ±1¢ band on **both** sides — so it can only ever collect exactly `total_fare`, never `grand_total`. The codebase's own authority (`routes/payments.py:_authoritative_ride_charge:54-76`) states `grand_total` includes area/booking fees **and GST/PST**; `total_fare` does not.

**Concrete:** fare $20.00 + $2 area fees + $2.31 GST/PST → `grand_total ≈ $24.31`. Wallet-settled rides collect **$20.00**; the $4.31 of fees + Saskatchewan tax is never collected on this path. The parallel `process-payment → settle_wallet` path (`routes/rides/payments.py:333`) correctly charges `grand_total + tip` — this second live endpoint was never updated.

**Verify first:** confirm whether the rider app's `payWithWallet()` (`rider-app/store/walletStore.ts:110`) actually routes through `/wallet/pay` or through `process-payment`. If the former, this is a live per-ride tax-remittance gap. **How to fix:** compute owed from `grand_total + tip` (reuse `_authoritative_ride_charge`), or retire `/wallet/pay` in favor of the unified settlement path.

### C4 — Insurance Period 1 fabricated for drivers who went offline mid-offer · CONFIRMED · **regulatory**
**`backend/routes/rides/matching.py:1139-1141`**

The batch-offer timeout release calls `set_driver_available(did, True)` then **unconditionally** `record_period_transition(did, 1)`, ignoring the return that says whether the driver actually became available. If the driver tapped "Go Offline" (or the presence sweeper flipped them) before the ~15s timeout, `is_available` is clamped to `False` (invariant needs `is_online`), yet a Period 1 (TNC contingent-liability) row is appended for a driver who is offline (Period 0). Insurance-period rows are **append-only and regulatory** — this is exactly the misclassification CLAUDE.md warns is an insurance/regulatory liability.

The single-offer sibling `_offer_timeout_handler:962-970` already guards this correctly (checks `released.get("is_available")`). Same missing guard (lower likelihood, same class) in `_release_loser` (`ride_flow.py:315`), `decline_ride` (`ride_flow.py:453`), rider cancel (`cancellation.py:322`).

**Fix:** Mirror the single-offer guard — only record Period 1 when the `set_driver_available` return confirms it stuck.

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

The **user-facing** error discipline is genuinely strong and ahead of typical pre-launch quality:

- `shared/api/client.ts` maps every backend error shape to a typed `SpinrApiError` / `RateLimitError` with i18n `messageKey`, `actionHint`, and a support `requestId`; raw stack traces never reach the UI.
- SOS is correctly exempted from the 401 interceptor (`isSosUrl`, `client.ts:649`) so a mid-trip token lapse can't bounce a rider to login during an emergency.
- GPS coordinates are redacted from URLs before logging/persisting (`_redactGpsUrl:591`); the persisted error ring-buffer drops raw bodies (PII discipline at rest).
- Backend uses `logger.opt(raw=True)` defensively and surfaces `DatabaseError.details['original']` per the CLAUDE.md rule.

**Admin/observability gaps that let real failures stay silent:**

- **T1 — Reconciliation is effectively non-functional at scale + floods false alerts · CONFIRMED.** `backend/utils/stripe_reconcile.py:133-152` fetches "yesterday's paid rides" with **no date filter and no `order`**, `limit=2000`, then filters to yesterday *in Python*. Past 2000 lifetime paid rides (immediate for any real platform), the arbitrary slice rarely contains yesterday's rows → genuine discrepancies go undetected **and** every succeeded Stripe PI from yesterday is flagged `STRIPE_ORPHAN` at ERROR → Sentry + `audit_logs`, drowning real signal. **Fix:** filter by `ride_completed_at`/`created_at` in the window server-side, add `order`, paginate.
- **T2 — Loop watchdog blind spots · CONFIRMED.** `core/lifespan.py:403-422` `_WATCHDOG_LOOP_NAMES` omits `safety_checkin (30s)`, `driver_claim_reaper`, `preauth_capture`, `referral_payout`, `reconciliation`, `suspension_reactivation`, `zoho_desk_sync`. A wedged **safety** loop would alert no one. **Fix:** derive watchdog names from the spawn set, or assert-at-boot that every heartbeating loop is registered.
- **T3 — No Sentry breadcrumb wired in the mobile client** (`client.ts:796` TODO) — payment failures can't yet be correlated mobile→backend→Stripe by `requestId`. Low effort, high support value.
- **T4 — Duplicate admin exhaustion alerts · CONFIRMED.** `payment_retry.py:490-526` — two alert sites, only one sets `admin_alerted_payment_exhausted`, so the exception/unexpected-state paths re-alert every admin next tick.

---

## 🐢 Performance Bottlenecks & Optimizations

- **P1 — Synchronous Stripe SDK calls block the event loop in background loops · CONFIRMED.** `stripe_reconcile.py:122-125,488` pages up to 10k PaymentIntents with blocking HTTPS on the asyncio loop; `payment_retry.py:331,356,423` does ~100 serial blocking Stripe calls/tick. During these windows the replica can't serve WS pings, dispatch fan-out, or `/health` — a rolling deploy could fail its health gate. **Fix:** `asyncio.to_thread` / `run_in_executor` each SDK call (or Stripe's async client); yield between pages.
- **P2 — WS Redis consumer delivers the whole fleet's messages serially · CONFIRMED (structural).** `utils/ws_pubsub.py:324-414` `_consumer` awaits each local socket send inline (up to the 2s send timeout) before reading the next message. One half-closed socket head-of-line-blocks every offer / `ride_taken` on that replica, breaching the `<100ms` fan-out and `<2s` dispatch SLAs under a reconnect storm. **Fix:** dispatch each message to a bounded worker pool / capped `create_task` instead of awaiting inline.
- **P3 — Heartbeat re-reads users/admin_staff every 10s per connection · CONFIRMED.** `routes/websocket.py:305-319` → ~100 Supabase reads/s baseline at 1k sockets, purely for revocation re-checks that the existing `publish_kick_user` fan-out already covers. **Fix:** rely on the kick channel and drop the per-tick DB read (or cache token_version with a short TTL).
- **P4 — Location batch rate-limiter does ~500 serial Redis INCRs per message · CONFIRMED.** `routes/websocket.py:910-912` loops `redis_incr` per point because there's no `INCRBY`. A 500-point recovery batch = 500 round-trips on the WS receive loop. **Fix:** add `INCRBY`/pipeline for one round-trip.
- **P5 — Admin analytics without cache** (backlog D7) — 5-min Redis cache on cancellation-breakdown would drop dashboard DB load ~98%.

---

## 💡 Tech Stack & Architecture Recommendations

The stack itself is modern and appropriate (FastAPI 0.136, Pydantic 2.13, Next 16/React 19, Expo 55, TanStack Query, Zustand). The gaps are **patterns and tooling**, not framework choices:

1. **Move blocking third-party SDK calls off the event loop (P1).** Either `asyncio.to_thread` at every Stripe/Twilio call site, or introduce a real task queue (**arq** or **Celery** with Redis) for reconciliation, payment-retry, and notification fan-out. Uber/Lyft never run heavy third-party I/O inline on the request/serving loop — this is the biggest architectural divergence.
2. **Redis config surface is inconsistent (see S1 below)** — unify URL resolution so leader locks can't silently degrade to per-process.
3. **Idempotency as a first-class primitive for internal money mutations.** Stripe webhooks are correctly idempotent, but corporate settlement (C-money findings) and the offline-queue replay (M-frontend) mutate without keys. Standardize a ride-scoped idempotency key threaded through every money delta and every client-replayed POST.
4. **Operational safety net (the real Uber/Lyft gap), from `ACTION_ITEMS.md` P4:** staging environment (E1, prereq for load test + migration rehearsal), external synthetic monitoring tied to the SLA table (E4), feature kill-switches for surge/dispatch/promo/corporate (E5), forced-upgrade gate for old mobile binaries (E3), and an exercised failover/restore drill (C1/E7).
5. **Async DB driver consideration (longer-term):** the `run_sync` thread-pool wrapper around `supabase-py` is a pragmatic bridge, but a native async Postgres path (asyncpg/PostgREST-async) for the dispatch hot path would remove thread-pool contention that P1/P3 currently aggravate.

---

## 🛠️ Maintainability & Code Smells

- **S1 — Leader locks ignore `RATE_LIMIT_REDIS_URL`/`WS_REDIS_URL` · CONFIRMED.** `redis_client.py:80` reads only `REDIS_URL`, but the boot warning advertises "`REDIS_URL` (or `RATE_LIMIT_REDIS_URL` + `WS_REDIS_URL`)". An operator who sets only the latter two gets `redis_set_nx` degrading to a per-process dict → every replica wins every leader election → reconciliation, scheduled-ride reminders, and the retry budget all multi-fire. Config contract doesn't match what the lock layer reads. **Fix:** resolve the URL from the promised precedence, or make the boot check require `REDIS_URL` specifically.
- **S2 — Two Stripe-customer-creation paths, only one PIPEDA-hardened · CONFIRMED.** `wallet.py:169-171` sends rider email + legal name to Stripe (US), directly contradicting the deliberate residency guard in `payments.py:120-138`. **Fix:** route top-up through the shared `get_or_create_stripe_customer` (metadata-only).
- **S3 — Duplicated timeout-release logic** across `matching.py`, `ride_flow.py`, `cancellation.py` with the insurance-period guard present in only some copies (root cause of C4). Extract one release helper.
- **S4 — Character-vs-byte length checks.** `websocket.py:682` enforces the 64KB cap with `len(raw)` (code points), so a multibyte payload can exceed 64KB on the wire. Use `len(raw.encode("utf-8"))`.
- **S5 — Minute-bucketed wallet top-up idempotency key** (`wallet.py:190`) isn't amount-scoped; two different amounts in one minute collide. Fold `amount_cents` into the key like the ride paths do.
- **S6 — Blocking `time.sleep` up to 6s at rate-limiter import** (`rate_limiter.py:52-68`) delays process readiness / deploy health windows. Bound it or move to async startup.
- **S7 — Migration numbering note in CLAUDE.md is stale** (says next slot 145; actual highest is ~230). Refresh, and land the cross-PR `CREATE OR REPLACE` target check already in flight.

**Money-flow correctness smells (need owner confirmation on intent):**
- **M1 — Corporate allowance settlement uses `apply_rollback` (credits master) · PLAUSIBLE.** `payment_service.py:419` debits a member's allowance via a *compensation/undo* primitive that does `master += amount, used += amount` (`migrations/29_corporate_allowance_rpc.sql:75-78`). Depending on whether allowances are pre-funded at grant, settlement either nets the company to zero cost (Spinr absorbs payout) or grows master on every allowance ride. Untested — the unit test mocks `apply_rollback` and never asserts the resulting balance direction. **Fix:** introduce an `allowance_consume` type that increments `used` without crediting master; confirm the reservation model first.
- **M2 — `settle_corporate` not replay-safe → double-debit · PLAUSIBLE→CONFIRMED.** `payment_service.py:418-483` passes no idempotency key to either corporate delta; the guest auto-settle crash path resets `processing→pending` (`:558-568`) and the retry sweep re-runs settlement → allowance `used` and master debited twice. **Fix:** thread a stable `ride_id`+phase idempotency key through `corporate_wallet_apply_delta`.
- **M3 — `charge.refunded` records cumulative `amount_refunded` per event · CONFIRMED.** `webhooks.py:848` → two partial refunds record −500 then −800 (should be −300), overstating the 7-year tax/GST-reversal ledger. **Fix:** record the incremental refund from `charge.refunds.data`, not `amount_refunded`.
- **M4 — Tip overflow charge PI never persisted · PLAUSIBLE.** `payment_service.py:673-708` — a second real card charge is created for over-buffer tips but only the hold PI is written to `financial_events`/`rides.payment_intent_id`; the overflow PI can't be matched on later refund/dispute. **Fix:** write a linked `financial_events` row for the overflow PI.
- **M5 — Stripe metadata `tip_amount` overstated by fees+tax · CONFIRMED.** `stripe_charge.py:177-179` derives `tip = total_amount − total_fare`, but the caller passes `grand_total + tip`, so metadata tip includes area fees + GST/PST — any report/T4A reading Stripe-side tip mis-attributes tax as driver tips. **Fix:** pass the real tip explicitly.

**Frontend (shared client) smells:**
- **M6 — Offline queue replays mutating POST/PUT/PATCH with no idempotency key · CONFIRMED.** `shared/api/offlineQueue.ts:152-158` replays on reconnect, and each replay gets a **fresh** `X-Request-ID` from `client.ts:33`, so the backend can't dedup. A request that succeeded server-side but lost its response on a network drop is re-sent → duplicate rides, double wallet top-ups, duplicate tips. Drop-oldest at capacity (`:62`) also silently discards a queued mutation with no user notice. **Fix:** thread a stable client-generated idempotency key per queued mutation; surface drops.
- **M7 — 503 retry replays non-idempotent POSTs · PLAUSIBLE.** `client.ts:759` retries any method on 503; a 503 after a partial backend write could double-submit. Mostly mitigated by backend idempotency where it exists — tighten alongside M6.

---

## 🧪 Testing & QA (missing edge cases)

- **Q1 — Per-module money coverage floors not enforced (`ACTION_ITEMS.md` A1, the biggest open gap).** CLAUDE.md mandates ≥90% for `routes/payments.py` + `services/fare_service.py` and ≥80% for `routes/rides.py` + `dispatch_service.py`; the global floor is 60%. Ratchet per-path with `coverage report --fail-under`.
- **Q2 — Corporate settlement balance direction is untested** (root of M1/M2). Add tests that assert the *resulting master/used balances*, not just the amount argument to a mocked RPC.
- **Q3 — Insurance-period regression coverage for the "driver went offline mid-offer" case** (C4) — add to the period-transition tests across all four release paths.
- **Q4 — Reconciliation-at-scale test** (T1): seed >2000 paid rides and assert yesterday's set is still found and no false `STRIPE_ORPHAN`.
- **Q5 — Partial/multiple-refund ledger test** (M3) and **tip-overflow PI persistence test** (M4).
- **Q6 — Offline-replay idempotency test** (M6): simulate success-then-lost-response and assert no duplicate side effect.
- **Q7 — Email-OTP lockout test** (C1) mirroring the phone-OTP lockout tests.

---

## 📈 Manager's Verdict — overall code health

**Grade: B+ engineering, C+ operational readiness. Not yet launch-ready, but close, and for tractable reasons.**

This is a well-architected, unusually disciplined codebase for its stage — the conventions in CLAUDE.md are real and largely honored in the code, the prior-audit trail is exemplary, and the user-facing error/telemetry hygiene beats most pre-launch platforms. The team clearly knows how to build this.

The risk is concentrated and consistent in character: **silent, scale-dependent, money-and-safety-path failures that a demo won't reveal.** None of the top findings are "the app crashes"; they are "the app looks fine while under-collecting tax (C3), halting payment retries once a backlog forms (P/retry-starvation), fabricating a regulatory insurance record (C4), or letting an attacker brute-force their way into any account (C1)." That profile is exactly what external monitoring, a staging environment, enforced money-coverage floors, and kill-switches exist to catch — and those are the operational pieces still missing.

**Recommended sequencing:**
1. **Launch-blockers, this week:** C1 (+C2 enabler) account takeover; C3 wallet tax under-collection (after verifying the live path); C4 insurance-period guard. All are small, localized diffs.
2. **Money integrity, before public launch:** M1/M2 corporate settlement (confirm intent, then fix sign + idempotency); M3 refund ledger; T1 reconciliation query.
3. **Reliability, before scale:** P1 (Stripe off the event loop) + S1 (Redis config unification) + payment-retry starvation/cadence — these compound each other under multi-replica.
4. **Operational net, in parallel:** staging (E1), synthetic monitoring (E4), kill-switches (E5), watchdog coverage (T2), and the money-coverage CI floor (Q1).

Close the launch-blockers and the money-integrity tier and this is a genuinely solid, launch-worthy platform. The bones are good; the remaining work is finishing the safety net, not rebuilding the house.

---

### Appendix — verified-solid areas (not re-flagged)
JWT trust model (non-admin role always re-read from DB; admin aud pinned; legacy no-aud path retired) · refresh-token rotation + reuse cascade · constant-time compares (OTP/CSRF/break-glass/bcrypt) · config fail-fast guards (JWT_SECRET≥32, ADMIN_PASSWORD, CORS, region) · new-table RLS (deny-all, no `FOR ALL`) · PII-safe logging on auth/users paths · acceptance-race atomicity (`ride_flow.py:205`) · rider-cancel-before-charge atomicity (`cancellation.py:92`) · `fare_service` Decimal cleanliness · webhook `claim_stripe_event` gating · `run_sync` circuit-breaker excludes application errors.
