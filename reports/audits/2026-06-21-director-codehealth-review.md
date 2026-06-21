# Spinr — Director-Level Code-Health Review
**Date:** 2026-06-21 · **Branch:** `claude/epic-planck-xenemb` · **Mode:** read-only (no code changed)
**Method:** 4 parallel grounded audits (security, money/fare, backend performance/dispatch, frontend/mobile) + direct reads of the error-handling boundary, dependency stacks, and recent fix commits.

> **Confidence labels.** `[HIGH]` = verifiable from the cited line, low chance of being intentional. `[VERIFY]` = plausible bug but may collide with a deliberate design choice in this codebase (which has prior "looks-like-a-bug-is-intentional" cases). Treat every `[VERIFY]` as "open a ticket and confirm before fixing," not "fix blindly."

---

## Executive framing vs. the market (Uber / Lyft)

Spinr is **not** an early-stage codebase. It is a mature, heavily-iterated platform: ~1,500 files, 248 backend test files, 16 replay-safe background loops, a circuit-breaker-protected DB layer, Redis pub/sub WS fan-out with outbox replay, a sanitizing global error boundary, refresh-token reuse detection (OAuth2 BCP §4.14.2), admin MFA + break-glass, and a Decimal-only money discipline enforced by a pre-commit hook. On *engineering hygiene* it already meets or exceeds what most regional rideshare startups ship.

Where Uber/Lyft are structurally ahead — and where the gaps below cluster — is in three areas Spinr should treat as its roadmap, not its emergency:
1. **Hot-path latency budgeting** (Uber dispatch is a dedicated low-latency service; Spinr does card pre-auth *inline* on the dispatch path — see §🐢).
2. **Geospatial matching at scale** (Uber uses H3 + purpose-built matching; Spinr does a 500-row driver pull + haversine pre-filter — fine at SK launch volume, a ceiling at city scale).
3. **Money-state completeness** (partial refunds, surge-as-line-item — see §🚨), the kind of long-tail financial-state handling that bites at volume.

The right read: **Spinr is launch-grade for Saskatchewan.** The findings below are a prioritized hardening backlog, not a "stop the launch" list — with the exception of the two `[HIGH]` financial/auth items in §🚨.

---

## 🚨 Critical Issues & Security Flaws

### C1 `[HIGH]` Partial Stripe refunds are mis-classified as full refunds
`backend/routes/webhooks.py:712–724` — the `charge.refunded` handler unconditionally sets `payment_status='refunded'` regardless of whether `amount_refunded < grand_total`. A $5 goodwill refund on a $20 ride flips the whole ride to `refunded`; a later refund of the remainder no-ops because the status is already terminal.
**Why it matters:** breaks refund/dispute accounting and the 7-year CRA tax-record reconciliation (refund amounts won't tie back to receipts). This *will* fire in production the first time support issues a partial refund.
**Fix (conceptual):** compare `amount_refunded` (cents) to the ride's `grand_total` cents; set `partially_refunded` + accumulate a `total_refunded` column when it's less than the total. Only go terminal when fully refunded.

### C2 `[HIGH]` Admin session endpoint trusts JWT claims without a DB re-read
`backend/routes/admin/auth.py:249–296` — `GET /admin/auth/session` reads `role`/`email`/`modules` straight from the decoded JWT and returns them, bypassing `_verify_admin_payload` (which every *other* admin route uses, and which checks `is_active`, `token_version`, idle timeout).
**Why it matters:** a staff member who was deactivated or had `logout-all` invoked (token_version bump) still shows as `authenticated: true` with their *old* role/modules until the 12h access token expires. This is the endpoint the dashboard uses to decide whether to render admin UI. This contradicts the CLAUDE.md JWT trust model, which mandates a per-request re-read for non-super-trusted state.
**Fix:** route `get_session` through `Depends(get_admin_user)` / `_verify_admin_payload` like the rest.

### C3 `[VERIFY]` Auth middleware auto-creates a user row on JWT user-not-found
`backend/dependencies/__init__.py:406–428` (and the Firebase path at 307–315) — when `get_user_by_id` returns `None`, the code creates a fresh `role: "rider"` user and proceeds. This is the exact shape of a CLAUDE.md-documented anti-pattern ("don't fall through to create-new-user … produced duplicate accounts").
**Why it matters / why VERIFY:** if this is the legitimate first-login provisioning path it's fine; if it's reachable with an arbitrary JWT `user_id` it lets an attacker mint ghost rows by probing UUIDs. Confirm whether the surrounding token audience/issuer checks already constrain this to a freshly-minted-but-unstored identity. If not, it should be a 401.

### C4 `[HIGH]` `checkout.session.completed` never stamps `processed_at`
`backend/routes/webhooks.py:636–699` — this branch returns without calling `mark_stripe_event_processed(event_id)`, unlike every other handler. The nightly reconciliation job treats a claimed-but-unstamped event as unresolved and replays it; the orphaned-subscription cancellation path (686–687) is **not** idempotent on replay → risk of cancelling an active driver subscription.
**Fix:** stamp `processed_at` at the end of the branch before returning.

### C5 `[VERIFY]` PII (raw staff email) written into `audit_logs.details`
`backend/routes/admin/auth.py:1383, 1395` pass `{"target_email": ...}` into `log_admin_action`, persisted verbatim into JSONB. The MFA provisioning URI (`:950`) similarly embeds the staff email. Both surfaces already have the staff `id` available.
**Why it matters:** PIPEDA bars email in logs/exports; the `audit_logs` table is a high-value breach target. Lower urgency than C1/C2 because it's an admin-only surface, but it's a clean compliance miss.

---

## 🛡️ Error Handling & Telemetry (user UX vs. admin observability)

**This is a genuine strength — call it out as such.**
- `backend/utils/error_handling.py` sanitizes all 5xx detail strings unless they match a vetted `ERR_[A-Z0-9_]+` sentinel allowlist; everything else is replaced with a generic message while full detail goes to the server log, **paired with the same `request_id` returned in the client body** (`_resolve_request_id`). Support can cross-reference a user-quoted id to exact log lines. This is better than most startups ship.
- Frontend mirrors it: `shared/api/client.ts:extractError()` → i18n key → backend message → generic fallback, with a 1s toast-dedup window (`rider-app/store/toastStore.ts`). Users never see `TypeError: …`.
- `X-Deadline-Ms` propagation lets the backend skip DB retries on already-doomed requests.

**Gaps to close (all `[HIGH]`, all small):**
- `backend/utils/refresh_tokens.py:133–135` — a DB error in `lookup_refresh_token` returns `None`, which the caller renders as a **401 → forces re-auth/session wipe**. A DB outage on the refresh path should be a 503 (client retries), not a mass logout. Add `exc_info=True` and raise `ServiceUnavailableException`.
- `backend/routes/admin/auth.py:619` — `logout-all` WS-kick failure logged at `warning` not `error` (violates the CLAUDE.md "never warn-and-continue on auth errors" rule; the rider/driver twin at `routes/auth.py:1199` does it right).
- `backend/routes/webhooks.py:742` — `charge.refunded` push failure logged at `debug`, so it's invisible in prod tailing/Sentry. Raise to `warning`+`exc_info`.

---

## 🐢 Performance Bottlenecks & Optimizations

Ranked by impact on the **<2s P95 dispatch SLA**:

1. `[HIGH]` **Card pre-authorization runs inline on the dispatch hot path** — `backend/routes/rides.py:1984–2030`. Stripe off-session confirm is ~500–1500ms and *blocks* the `/rides` response before driver search begins, with a retry on failure. On card-paying riders this alone can consume the entire SLA budget. **Fix:** issue the hold async (`asyncio.create_task`), start dispatch immediately, surface "hold pending" in-app if it fails — the same non-blocking pattern already used for push notifications at `:1019`.
2. `[HIGH]` **N+1 `quest_progress` lookup per claimed driver** — `backend/routes/rides.py:920–943`, one serial Supabase round-trip per driver inside the notification loop (~120–240ms for 3 drivers). **Fix:** batch with `.in_(driver_ids)` once before the loop.
3. `[MED]` **500-driver candidate cap with no overflow handling** — `rides.py:706–710`. Fine for SK launch; at >500 online drivers in an area the 501st is silently invisible. Document the cap or paginate.
4. `[MED]` **No-driver retry cadence** — 10s between dispatch retries means a ride can sit in `searching` ~50s before the stuck-ride sweeper cancels it. Consider 5s for the first 2–3 attempts.
5. `[LOW]` Per-user WS rate limit (30 msg/s) is per-replica, not cross-replica (known, B-P1-12). Admin location broadcast already throttled to 0.33Hz with a 2s per-socket write timeout — good load-shedding.

**Well-designed (don't regress):** batched Redis MGET for offer-skip/presence; single batched Distance-Matrix ETA call (top-15 pre-filter) with 3s timeout + haversine fallback; atomic SQL driver claim (`WHERE is_available=true`); `asyncio.gather` enrichment; circuit breaker + per-second retry budget + deadline-aware retries on the DB executor (64 threads).

---

## 💡 Tech Stack & Architecture Recommendations

**Current stack is modern and well-chosen:** FastAPI 0.136 / Python 3.12 (SHA-pinned image), Supabase+RLS, Redis pub/sub with in-process fallback, Stripe, Expo SDK 55 / RN 0.85 / React 19, Next.js admin, Zustand, Sentry across all surfaces. No glaring "wrong tool" choices.

Gaps worth filling, in ROI order:
- **Dedicated job queue for side-effects.** Today Stripe/Twilio/FCM calls are mitigated with `asyncio.create_task`, and durability rests on 16 in-process loops + DB claim flags. At volume, adopt **Celery/RQ/Arq or a Redis Streams worker** so payment captures, pushes, and reconciliation survive a pod restart mid-task and get real retry/DLQ semantics. This also gets card pre-auth (§🐢 #1) off the request path cleanly.
- **Geospatial indexing.** Replace the 500-row pull + Python haversine with **PostGIS `ST_DWithin` + GiST index**, or **Uber's H3** for cell-based candidate selection. This is the single change that moves dispatch from "launch-scale" to "city-scale."
- **Read-replica / cache for dashboard reads.** Admin analytics + ride lists are the classic N+1/full-scan surface; a read replica or a short-TTL materialized cache protects the primary.
- **Cross-replica WS rate limiting** via a Redis sliding-window counter (closes B-P1-12).
- **Contract tests for Stripe webhooks** generated from Stripe's fixtures — see §🧪.

Architectural risk to name explicitly: **the single-process-with-16-loops model is elegant but couples web-serving and scheduled work in one deploy unit.** It's correct today (every loop is replay-safe via claim flags / NX locks). The moment you need to scale web throughput independently of background cadence, split the scheduler into its own worker deployment.

---

## 🛠️ Maintainability & Code Smells

- **Monolithic RN screens:** `rider-app/app/ride-options.tsx` (1,988 LOC), `ride-completed.tsx` (1,156), `ride-status.tsx` (997). Orchestration + rendering + business logic in one export → test fragility and onboarding cost. Extract pure presentational components (`RatingCard`, `TipPicker`, `FareBreakdown`, `PaymentPicker`).
- **`as any` scatter:** ~36 instances in admin-dashboard (highest density), plus polyline casting in `ride-completed.tsx:89–94`. Define `RideWithRoute` union types; replace casts with a `toRouteCoords(raw?: unknown)` guard. Each cast is a runtime error waiting on a typo'd field.
- **Migration-number collisions** (08, 28, 29, 48, 50–58, …) — already documented and handled by full-filename idempotency keying + a CI prefix-uniqueness gate. Not a bug; flagged so a future author doesn't "fix" it by renaming (which would break the idempotency key).
- **Naming clarity:** `admin_earnings` (booking/airport fee) reads like a per-trip commission but is a disclosed operating fee, and `corporate_allowance_service.apply_rollback` is used as the *primary debit* path (§money V-items). Both deserve a clarifying comment to prevent a future contributor "correcting" them.

---

## 🧪 Testing & QA (missing edge cases)

Baseline is strong: 248 backend test files, RN/admin suites, E2E ride-lifecycle Playwright specs, `--cov-fail-under=60` (note: domain minimums in CLAUDE.md are higher — payments ≥90%, rides ≥80% — the global gate is laxer than the stated policy; consider per-package gates).

Edge cases the audits surfaced that need a regression test added:
- **Partial refund** (C1) — assert `partially_refunded` + subsequent-refund accumulation. None exists today.
- **`checkout.session.completed` idempotency** (C4) — assert `processed_at` is stamped and replay is a no-op.
- **Deactivated-admin session** (C2) — assert `GET /admin/auth/session` returns unauthenticated after `logout-all`.
- **Refresh-path DB outage** — assert 503 (not 401) when the token store is unreachable.
- **Surge-as-line-item** (§money) — assert the receipt renders a dollar surge line, not just a notice.
- **Card-rejection retry** is well-covered already (route-param carry + `hasRatedRef` latch preventing double driver-credit — verified in `ride-completed.tsx`).

---

## 💰 Money/Fare specifics (verify-first cluster)

The Decimal discipline is genuinely solid — `utils/money.py` avoids the `int(amount*100)` truncation bug, Stripe gets integer cents, surge cap 2.5× is enforced, corporate deltas go through the SECURITY DEFINER RPC, 3DS/`requires_action` is handled, PCI boundary rejects raw PANs. The items below are `[VERIFY]`:
- **Surge not shown as a $ line item** on the receipt (`utils/email_receipt.py:128–135`) — shown as a notice, not an itemized debit. Regulatory transparency ("no hidden fee") wants it itemized. *High-confidence finding, regulatory.*
- **`float(val)` on receipt fee amounts** (`fare_service.py:265,273,275`) — drift risk at the rider-facing breakdown boundary; use `_f(Decimal(str(val)))`.
- **Float literals in `DEFAULT_FARE`** (`fare_service.py:31–37`) — contained today (all reads go via `_d()`/`str()`), but a latent contamination source; declare as strings.
- **Base fare excluded from surge** (`fare_service.py:204–207`) — surge multiplies distance+time only, not `base_fare`. The agent flagged this as a spec deviation; **this is most likely intentional** (booking fee is also excluded) but the *docs and code should be made to agree explicitly* so it's not "fixed" by accident in either direction.
- **`apply_rollback` used as the primary allowance debit** (`payment_service.py:281–286`) — verify the Postgres RPC `type='allowance_rollback'` decrements (not increments) `used`; rename to `apply_debit` if confirmed.

---

## 📈 Manager's Verdict

**Overall code health: B+ / launch-ready for Saskatchewan, with a short list of must-fix-before-volume items.**

This is a disciplined, security-conscious, well-tested codebase that has clearly been through serious hardening sprints. The conventions in CLAUDE.md aren't decorative — they're reflected in the code (Decimal money, sanitized errors, replay-safe loops, refresh-token reuse detection). That's rare and worth protecting.

The risk profile is **long-tail financial/auth state-handling**, not foundational design:
- **Fix now (this sprint):** C1 (partial refunds) and C2 (admin session JWT trust) are real, will fire in production, and are small. C4 (webhook idempotency stamp) is one line. Surge-as-line-item is a regulatory checkbox.
- **Verify then fix:** C3 (auto-create user), C5 (PII in audit), and the money `[VERIFY]` cluster — each one ticket, confirm-then-act.
- **Roadmap (pre-scale, not pre-launch):** move card pre-auth off the dispatch path, adopt a real job queue, move matching to PostGIS/H3, split the scheduler deployment.

Against Uber/Lyft, the gap is **scale-engineering, not correctness-engineering.** Spinr's differentiators (0% commission, SK regulatory fit, PIPEDA-native, surge hard-cap) are baked into the architecture, not bolted on — which is the harder thing to get right and they've largely got it. Ship the C1/C2/C4 fixes, file the rest, and this is a defensible launch.

---
*Read-only review. No files modified. Findings cite real lines; `[VERIFY]` items intentionally stop short of asserting a fix to avoid "correcting" deliberate design.*
