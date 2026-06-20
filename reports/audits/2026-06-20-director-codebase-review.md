# Spinr — Engineering Director Codebase Review

**Date:** 2026-06-20 · **Scope:** read-only teardown (backend-weighted) · **Reviewer:** automated director review (4 parallel audits: security, money, performance, telemetry), with key money/security claims hand-verified against source + domain specs.

**Bottom line:** This is a mature, unusually disciplined codebase — fail-fast config, idempotent Stripe webhooks, append-only audit/RLS conventions, Prometheus + Sentry wiring, and a documented ride-state machine put it ahead of most Series-A-stage rideshare backends. It is *not* an Uber/Lyft peer on scale-engineering (no async DB driver, two 5k–7k-line god-files, dispatch latency risk), and there is a **confirmed money/tax leak on the wallet payment path** plus a **JWT trust gap** that should block launch. Findings below are graded; each "critical" was verified against source, not taken on the audit's word.

> **Note on verification:** One headline audit finding — "surge formula excludes base fare → $5.25 undercharge/ride" — was a **false positive**. `domain-payments.md:26-31,49` mandates surge applies to distance + time *only*, never base; `fare_service.py:204-207` implements the spec correctly. It is excluded from this report. The wallet-tax finding below *was* confirmed against the authoritative charge path.

---

## 🚨 Critical Issues & Security Flaws

| # | Finding | Location | Why it matters |
|---|---------|----------|----------------|
| C1 | **Wallet payment under-collects tax + skips tip.** Card path charges the server-authoritative `grand_total` (incl. GST 5% / PST 6% + area fees) via `_authoritative_ride_charge`; the wallet path charges raw `total_fare`, which is the **pre-tax legacy** amount. | `routes/wallet.py:242` vs `routes/payments.py:51-69` | Every wallet-settled ride in a taxed area **under-collects GST/PST** (CRA liability) and collects no tip. Riders are incentivized to the cheaper, non-compliant path. **Confirmed against source.** **Fix:** route wallet pay through the same `_authoritative_ride_charge` (grand_total + tip) authority; treat `total_fare` as legacy-only fallback. |
| C2 | **JWT primary decode disables audience check + auto-creates users on miss.** `verify_jwt_token` decodes with `verify_aud: False`; `get_current_user` *creates a new rider row* when a signed token's `user_id` isn't found. | `dependencies/__init__.py:118, 406-417` | A signed token for a deleted/orphaned account silently provisions a phantom account — the exact "never fall through to create user → duplicate accounts" anti-pattern CLAUDE.md calls out. **Fix:** decode with `audience=JWT_AUD_MOBILE`; on missing user raise 401, never create. New-user creation belongs only in `/verify-otp` + `/firebase`. |
| C3 | **Full phone number embedded as a plaintext JWT claim.** `"phone": phone` in the mobile token payload. JWTs are base64, not encrypted. | `dependencies/__init__.py:103` | Any access log / proxy / SIEM that captures a Bearer token captures a full E.164 number — PIPEDA breach surface. **Fix:** drop the `phone` claim; read phone from DB where needed (it's the source of truth anyway). |
| C4 | **Client-controlled Stripe idempotency key, un-namespaced.** `client_idempotency_key` used verbatim. | `routes/payments.py:206-213` | A crafted key can collide with another user's Stripe idempotency slot (cross-user interference). **Fix:** prefix with authenticated `user-{id}-`. |
| C5 | **OTP not invalidated on double-failure, yet tokens still issued.** On delete+update both failing, the OTP row stays live and tokens are issued anyway. | `routes/auth.py:459-474` | A reusable OTP is an auth-bypass primitive if verify logic ever shifts from hash-match to flag-check. **Fix:** raise 503 if the OTP cannot be invalidated; never issue tokens over an un-retired OTP. |

**Verified-clean (don't re-litigate):** wildcard-CORS hard-blocked in prod (`middleware.py:531`); production startup guards on JWT length / admin password / Supabase region / Firebase audience (`config.py:204-271`); Stripe webhook idempotency gate (`claim_stripe_event`) on every event; router-level admin auth dependency (no unauthenticated admin routes); break-glass endpoint hardening; OTP SHA-256 + constant-time + lockout.

---

## 🛡️ Error Handling & Telemetry (user UX vs. admin observability)

**Strengths:** No bare `except: pass` swallow-and-continue found on critical paths. HTTPException detail is scrubbed before reaching clients (no raw `str(e)` leak). Redis client degrades gracefully to an in-process dict. loguru→Sentry bridge is wired.

**Gaps to close:**
- **Payment-failure events logged at `warning`, not `error`** (`webhooks.py:557`; admin WS-kick `admin/auth.py:619`). Per CLAUDE.md, money/auth failures must be `logger.error` to page on-call. As-is, failed payments and failed admin session-kills **don't reach Sentry**.
- **Settings-fetch failures silently fall back to hardcoded defaults** on dispatch-affecting paths (`rides.py:1098-1100, 3304, 3342, 3388`). An admin changes offer-timeout / no-show window in the DB; if the settings table hiccups, drivers silently get the wrong behavior with **no metric to alert on**. Add a `spinr_rides_settings_fetch_failed_total` counter; log at error on the dispatch-critical ones.
- **Payment settlement path is not instrumented** — no `spinr_payment_settlement_total{outcome}` / `_duration_ms`. Finance can't watch payment health without DB polling, and the SLA dashboard can't measure P95 settlement (`services/payment_service.py`).
- **Sentry domain tags inconsistent.** The scrubber lifts tags from loguru `extra={}`, but many payment/auth error logs omit `extra={"domain":..., "ride_id":...}` — events arrive tagged only `surface=backend`, killing triage-by-domain.
- **PII in logs:** raw Firebase UID logged (`auth.py:737`) — a persistent device identifier (PIPEDA). Use the existing `utils/pii.redact_phone()` / a hashed UID rather than ad-hoc `phone[-4:]` slicing.
- **503 vs 500 semantics:** some transient DB races raise 500 (mobile treats as permanent) where 503 would let the Axios interceptor retry (`drivers.py:5663`).

---

## 🐢 Performance Bottlenecks & Optimizations

Ranked by ROI against the stated SLAs (dispatch P95 < 2s, fare settlement < 1s):

1. **Synchronous Stripe in the request handler.** `stripe.PaymentIntent.create/confirm` are blocking calls awaited inline in the ride-create pre-auth path (`utils/stripe_charge.py:209,215` ← `rides.py:2610`). Adds 200–500ms (up to 10s on timeout) to the highest-volume endpoint and risks thread-pool starvation. **Fix:** background the auth, or move to post-acceptance settlement; add a Stripe circuit-breaker.
2. **N+1 in the dispatch hot loop.** Per-driver quest-progress lookups (`rides.py:915-943`) and per-driver fresh-status re-fetch (`rides.py:799-813`) issue one query per offered driver (×3 default). ~450–600ms added to every dispatch — alone a P95 risk. **Fix:** batch with `.in_(driver_ids)` before the loop; cache quest state in Redis.
3. **N+1 in the driver-claim reaper** (`utils/driver_claim_reaper.py:106-107`): two queries per candidate ×N drivers every 60s. **Fix:** one batched `.in_()` fetch → in-memory set membership.
4. **No async Postgres driver.** All DB access is `supabase-py` (sync) marshalled through a 32-worker ThreadPoolExecutor (`db_supabase.run_sync`). Functional, but the thread pool is the real concurrency ceiling and the source of tail latency under load. **This is the single biggest scale-architecture gap vs. Uber/Lyft-grade backends.** **Fix (strategic):** introduce `asyncpg` / SQLAlchemy-async for hot read paths (dispatch candidate query, ride lookups) while keeping supabase-py for RLS-bound writes.
5. **Unbounded background-loop queries** — some of the 16 startup loops lack explicit `LIMIT`/pagination (`stuck_ride_sweeper`, `payment_retry`); a backlog spike pulls large result sets into memory.
6. **Blocking Twilio SMS inline** in the safety/completion path (`rides.py:4703-4731`) — N×Twilio RTT on ride completion. Fire-and-forget via `asyncio.create_task` + push-retry loop.
7. **Index coverage** — confirm composite indexes exist for the hot patterns: `rides(rider_id, created_at DESC)`, `quest_progress(driver_id, status)`, `ride_offers(driver_id, status)`, `rides(status, started_at)`. Several background loops imply sequential scans.

Estimated combined effect if 1–3 land: dispatch P95 ~1.8s → ~1.2s; ride-create P95 ~2.0s → ~1.5s.

---

## 💡 Tech Stack & Architecture Recommendations

**The stack is modern and well-chosen** — FastAPI 0.136, Next 16 / React 19, Sentry, Prometheus exposition, Fly/Railway dual-deploy with DNS failover, Supabase RLS, comprehensive CI (15+ workflows incl. security-gates, migration-safety, pip-compile drift, subprocessor monitor). Few gaps, but the ones that exist matter at scale:

- **Async DB driver (asyncpg)** — see Perf #4. Highest-leverage architecture investment.
- **Caching layer formalization** — Redis is present but used ad-hoc (and re-implemented inline in `maps_proxy.py` instead of via `redis_client`). Introduce a thin cache decorator + consistent TTLs for fare-config, service-area, and reverse-geocode reads; emit cache-hit/miss metrics.
- **Circuit breakers for upstreams** — Stripe / Twilio / Google Maps are awaited with default SDK timeouts and no breaker. Add `pybreaker` (or equivalent) + aggressive client timeouts so an upstream brownout degrades gracefully instead of cascading into request latency.
- **Three LLM SDKs bundled** (`anthropic`, `openai`, `google-generativeai`) in backend deps. Consolidate behind one provider abstraction unless all three are genuinely in production use — each is a dependency, attack, and version-drift surface.
- **Outbound work queue** — "queue Twilio/Stripe side-effects" is currently `asyncio.create_task`. Fine pre-launch; at scale a real broker (Redis Streams / RQ / Celery) gives retries, DLQ, and back-pressure that bare tasks don't.

---

## 🛠️ Maintainability & Code Smells

- **God-files.** `routes/drivers.py` (6,826 lines) and `routes/rides.py` (5,562 lines) each span ~38 endpoints across many domains (onboarding, earnings, documents, insurance, quests / dispatch, payment, settlement, safety, history). This is the top maintainability risk: hard to reason about, hard to localize a regression, heavy mocking to test. **Recommend** decomposing along the lines already used in `services/` (e.g. `drivers/onboarding.py`, `drivers/earnings.py`, `rides/dispatch.py`, `rides/settlement.py`) — incrementally, behind the existing router mounts.
- **Money-display path inconsistencies.** `_f()` at JSON-serialization boundaries is fine (compute-in-Decimal, emit float at edge), but a few genuine float-arithmetic-then-`round()` sites remain in the driver-earnings enricher (`rides.py:3435-3452`, `drivers.py:3270-3298`) — penny drift that compounds into T4A summaries. Route through `_d()`/`_round()`.
- **Receipt line-item granularity.** Base + distance + time are collapsed into one "Ride fare" line (`fare_service.py:243`). CLAUDE.md's transparency contract ("every charge maps to a disclosed line item") and the SK regulatory receipt format want these itemized. Confirm GST and PST always render as **two separate** lines (the `tax_breakdown` dict has no enforced GST/PST key presence).
- **Refund ledger gap.** `charge.refunded` updates the ride to `refunded` but writes no `financial_events` row (`webhooks.py:667-718`) → admin revenue dashboards double-count refunded rides; reconciliation/T4A drift.
- **Admin lint runs with `--max-warnings 600`** — a large suppressed-debt ceiling that hides regressions. Ratchet downward.

---

## 🧪 Testing & QA (missing edge cases)

Strong baseline: ride-state-machine tests, E2E ride lifecycle (Playwright), money/Decimal tests, per-domain coverage targets, Stripe-webhook-type coverage requirement. Gaps:

- **Coverage gate is below policy.** `pytest.ini` sets `--cov-fail-under=60`, but CLAUDE.md mandates payments/fare/crypto ≥90%, rides/dispatch ≥80%. The global gate doesn't enforce the per-domain floors — a payments regression can land under 90% and still pass CI. **Add per-package coverage thresholds.**
- **Missing edge-case tests implied by findings above:** wallet-pay-with-tax (C1), JWT-missing-user-must-401 (C2), client-idempotency-key-collision (C4), OTP-double-failure-must-503 (C5), wallet path uses grand_total, refund writes financial_events, `requires_action` (3DS) on payment-retry leaves ride notified-not-stranded.
- **No load/concurrency test on dispatch** despite a 2s P95 SLA and the N+1s above. Add a perf-baseline that fans out concurrent ride requests against the dispatch path.

---

## 📈 Manager's Verdict

**Overall health: B+ / strong-but-launch-gated.** The conventions, CI discipline, regulatory awareness (PIPEDA, SK Transportation Act, SGI insurance periods), and prior P0 sprint execution are genuinely above peer for this stage — this team writes down its invariants and enforces them. That same rigor is why the residual issues stand out.

**Must-fix before launch (P0):** C1 wallet tax leak (financial + CRA), C2 JWT audience/auto-create (auth bypass + duplicate accounts), C3 phone-in-JWT (PIPEDA). These are small, surgical diffs — days, not weeks.

**Next (P1):** background the Stripe call + the two dispatch N+1s (protects the 2s SLA the whole product is sold on); promote payment-failure logs to `error`; instrument settlement metrics; per-domain coverage gates.

**Strategic (this/next quarter):** async DB driver, decompose the two god-files, formalize caching + circuit breakers. None gate launch; all gate *scale*.

**vs. Uber/Lyft:** Spinr's product differentiation (0% commission, hard 2.5× surge cap, Canadian-regulatory-first) is cleanly encoded in the code, not bolted on — that's a real moat. Where it trails the incumbents is pure scale-engineering: synchronous DB access, monolithic route files, and dispatch latency headroom. Those are normal for this stage and tractable with the items above; none are architectural dead-ends.
