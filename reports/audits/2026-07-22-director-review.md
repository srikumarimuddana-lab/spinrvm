# Spinr — Director-Level Code & Architecture Review

**Date:** 2026-07-22
**Scope:** Read-only teardown of backend (dispatch/rides/fare, payments/wallet/corporate, auth/telemetry), frontend surfaces (rider/driver/admin/shared), and a market/tech-stack benchmark vs. Uber/Lyft.
**Method:** Five parallel review passes, each reading the actual code and verifying claims against it (file:line-anchored). This document is a plan-of-record, not a rewrite.

---

## 📈 Manager's Verdict (read this first)

Spinr is a **genuinely well-engineered monolith that is honest about its own gaps**. The hard problems most ride-share clones get wrong are already right here: the acceptance CAS race, batch-offer idempotent expiry, refresh-token rotation with reuse-cascade, replay-safe background loops, fail-closed rate limiting, and PIPEDA PII-in-logs discipline are all correctly implemented and were verified against the code. The team's own `ACTION_ITEMS.md` tracks most operational gaps in roughly the right priority order — an unusually mature backlog.

The findings below cluster into two themes:

1. **Consistency bugs** — a handful of money/regulatory paths that *don't* apply a safety pattern the codebase already implements correctly everywhere else (rating idempotency, tax-on-wallet-pay, insurance-period guards, earnings snapshot). These are the highest-leverage fixes because the correct pattern already exists to copy.
2. **One un-tracked architectural ceiling** — the synchronous PostgREST-over-HTTP data tier — plus a PostGIS index the team already built and pays to maintain but the dispatch hot path refuses to use.

**Overall health: strong (B+/A-).** No rewrite warranted. Three exploitable/regulatory issues should be fixed before scaled launch; the rest is a well-ordered backlog. Nothing here contradicts Spinr's guardrails (0% commission, 2.5× surge cap, contractor model, no data-harvesting) — and several tempting "Uber growth levers" are correctly ruled out by them.

---

## 🚨 Critical Issues & Security Flaws

Three items are exploitable or carry regulatory/financial exposure. All were verified against the deployed code path (including the backing SQL RPCs).

### C1 — `rate_driver` has no idempotency guard → double-charged tips + duplicated rating votes (MONEY)
`backend/routes/rides/rating.py:34-105`
The handler checks only `status == COMPLETED` (L47); it never checks whether the rider already rated. On any retry/double-submit it **re-adds the tip** (`new_tip = existing_tip + delta`, `driver_earnings += delta`, L71-74) and re-applies the rolling rating average + `total_ratings` bump (L92-105). A slow response + client retry → rider over-charged, driver payout ledger corrupted, driver gets 2 rating votes from one ride.
- **Why it matters:** the tip is a real money figure consumed downstream by pre-auth capture. This is the *one* place in the flow missing the idempotency the accept/complete paths go to great lengths for.
- **How:** atomic conditional update guarding `rider_rating IS NULL` (CAS); skip all side-effects if it matched zero rows. Add a regression case to `test_ride_state_machine.py`.

### C2 — `/wallet/pay` undercharges GST/PST + area fees → tax under-collection (REGULATORY, exploitable)
`backend/routes/wallet.py:251-256` → RPC `backend/migrations/110_wallet_pay_for_ride_tip_atomic.sql:35-115`
The wallet path validates the client amount against `total_fare` (the **pre-tax** fare subtotal) and the RPC does **no** server-side fare check — it debits whatever it's given and marks the ride `paid`. But `grand_total = total_fare + area_fees + tax_amount − discount` (`routes/rides/booking.py:634`). A rider settling a completed ride via `POST /wallet/pay` pays only the pre-tax subtotal and **escapes remitted GST (5%) + PST + area fees**. The Stripe paths correctly charge `_authoritative_ride_charge = grand_total + tip` (`routes/payments.py:54-76`).
- **Why it matters:** under-remitting GST/PST is a CRA/provincial tax-compliance exposure, and receipts stop reconciling to collected tax. The dead `fare_underpaid`/`ride_not_payable` guards in `repositories/wallet_repo.py:132-135` (the RPC raises neither) give false confidence that this is covered.
- **How:** validate/debit against `grand_total (+ tip)`, matching the card path. One-line-of-intent change; add a fare-branch test.

### C3 — Corporate email-OTP login has no brute-force lockout on a 4-digit code → account takeover (SECURITY, OWASP A07)
`backend/routes/auth.py:637-702` (`verify_company_email_otp`); OTP is 4-digit / 5-min (`dependencies/__init__.py:47,51`)
The phone-OTP path deliberately layers a per-account 5-fail/hour → 24h lockout (`_check_otp_lockout`/`_record_otp_failure`) *because* the IP limiter is bypassable by IP rotation (the code says so at `auth.py:130-135`). The **email path has only `@limiter.limit("5/minute")` per IP** — no per-account counter, no lockout, and it does **not invalidate the OTP row on a wrong guess**. The same 10⁴-space code stays valid for the full 5 minutes. ~100 rotating IPs ≈ 2,500 guesses per code ≈ 25% takeover/cycle; resend+repeat → near-certain takeover of any corporate email account (and, via `_activate_pending_company_invites`, its pending memberships).
- **How:** reuse the existing `_check_otp_lockout`/`_record_otp_failure` keyed on email (fail-closed), and rotate/delete the OTP row after N failures. The pattern already exists two functions away.

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

**The bar is already high and mostly met.** Verified-correct: DB failures consistently log `e.details["original"]` and return clean 503/502 (`auth.py`, `dependencies/__init__.py`, `websocket.py` closes 1013 rather than downgrading an outage to `invalid_token`); the unauthenticated `verify_otp` catch-all refuses to interpolate `{e}` into client output; the global handler returns generic messages (`middleware.py:642-680`) — **no stack-trace leakage to end users**. PII-in-logs discipline is strong (phones as `[-4:]`/SHA-256, emails as digest ids, everything else keyed on `user_id`; a grep found no raw GPS/phone/email/name in log or Sentry calls).

Residual items:

- **H4 — Insurance-period audit writes a false Period-1 row (REGULATORY audit integrity).** `backend/routes/drivers/ride_flow.py:344-346` (`_release_loser`) and `:488-489` (`decline_ride`) call `record_period_transition(id, 1)` without the `released.get("is_available")` guard that the offer-timeout handlers use (`matching.py:1022-1023`, comment: *"recording Period 1 here would falsely reopen a commercial-insurance window for an offline driver"*). If a losing/declining driver went offline in the window, the append-only insurance audit shows a TNC commercial-coverage window that never existed. **Fix:** apply the same `is_available` gate in both spots.
- **F3 — Wallet top-up leaks rider email + legal name to Stripe (PIPEDA).** `backend/routes/wallet.py:170-172` calls `stripe.Customer.create(email=…, name=…)`, contradicting the deliberate guard in `routes/payments.py:127-141` (`get_or_create_stripe_customer` sends only `metadata.user_id`). Same idempotency key `cus-create-{user_id}` → whichever path runs first wins; if top-up wins, PII crosses to a US processor with no functional need. **Fix:** route through the shared `get_or_create_stripe_customer`.
- **Frontend UX-3 — Admin/company surfaces render raw `e.message`.** `admin-dashboard/src/app/company-login/page.tsx:50,102` (rendered L173), `referral-analytics.tsx:44→104`, `company-signup/page.tsx:306` surface the browser's raw `"Failed to fetch"`/`"NetworkError…"` on a fetch-layer failure. Backend `detail` messages are curated, but the network layer isn't. **Fix:** branch on `TypeError`/network errors → friendly connectivity copy.
- **L — Silent WS frame-parse `catch {}`.** `useDriverDashboard.ts:1118`, `useRiderSocket.ts:294` — a *systematically* malformed server message type is invisible; add a dev-gated `console.warn`.
- **Low (security telemetry):** `/admin/auth/session` (`admin/auth.py:248-296`) reports revoked/deactivated admin tokens as `authenticated:true` (stale UI only — every real endpoint re-checks via `_verify_admin_payload`); email-OTP **send** has no per-destination cap (`auth.py:589-634`) → inbox-bomb / SES-reputation abuse.

---

## 🐢 Performance Bottlenecks & Optimizations

Anchored to the P95 SLAs in CLAUDE.md.

- **P1 — PostGIS index is built and maintained but the dispatch hot path won't use it.** Migration 170 adds a trigger-maintained `location_geog` geography column + partial GiST index + a radius RPC, but `routes/rides/matching.py:229-231` reads via PostgREST bounding box and runs **Python haversine over `LIMIT 500`** rows. Dispatch pays for the index *writes* on every heartbeat, takes none of the read benefit, and the hard 500-row cap can report a false "no drivers" above 500 online drivers province-wide — against a **<2s dispatch P95 SLA**. **Cheapest high-impact fix in the report:** call an `ST_DWithin(location_geog, pickup, radius)` RPC in place of the bounding-box scan; keep the Redis presence filter in Python.
- **M5 — N+1 `quest_progress` query inside the dispatch notify loop.** `matching.py:795-822` parallelizes shared enrichment via `asyncio.gather` but then issues a **separate `quest_progress` select per claimed driver** (up to 10), plus serial `send_personal_message` Redis round-trips — the serial tail that risks the <2s SLA under a full batch. **Fix:** batch quest progress via one `.in_()` before the loop.
- **M4 — Surge counting re-scans the entire drivers table once per service area.** `surge_engine.py:161-167` fetches up to 5000 drivers, MGETs presence, runs point-in-polygon, throws the pool away, and refetches for the next area; `get_surge_status` (admin dashboard) does this for demand *and* supply per area on every poll. Egress + dashboard latency scale with area count × replicas. **Fix:** fetch the driver set once, bucket by polygon in memory (the `_SURGE_SPATIAL_COUNT` flag / migration 170 is the real fix, currently off).
- **Frontend M1 — Rider WebSocket has no heartbeat/half-open watchdog → frozen driver map (safety-adjacent).** `rider-app/hooks/useRiderSocket.ts` replies `pong` but never records a last-server-message timestamp and has no force-close interval; the driver hook does exactly this (`useDriverDashboard.ts:1263-1271`). On a silent cellular NAT drop (no FIN), `onclose` never fires and the rider's live map of the approaching driver freezes with no reconnect. **Fix:** mirror the driver's `lastServerMsgRef` + missed-ping watchdog. (Also: rider reconnect uses fixed ±500 ms jitter with no attempt cap — thundering-herd risk the driver hook already abandoned.)

---

## 💡 Tech Stack & Architecture Recommendations

### The one un-tracked ceiling: the data tier
Every one of the ~66 DB helpers runs the **synchronous** `supabase-py` client (PostgREST + httpx) inside a **64-thread executor per replica** (`repositories/_base.py:138`, `DB_THREAD_POOL_SIZE=64`). Consequences vs. an Uber/Lyft data tier: all queries go through the PostgREST REST API (no prepared statements, no direct SQL joins beyond RPCs), concurrency is hard-capped at 64 in-flight ops/replica, and you scale only by adding replicas — each of which adds 64 threads *and* re-runs all background loops, multiplying Supabase/PostgREST pressure non-linearly. There is no async pg driver in `requirements.txt` (`psycopg2-binary` is sync, migration-only). **This is the real "won't scale like Uber" item and it is not on the backlog.**
- **Concrete next step (additive, not a rewrite):** adopt `asyncpg` + the Supabase transaction pooler (`:6543`, pgbouncer) **for the hot paths only** — dispatch candidate fetch, ride state transitions, wallet deltas — behind the existing `repositories/` interface. Leave the long tail on `supabase-py`. This removes the hottest queries from the thread pool and gives prepared statements.

### Loops share the request event loop
`core/lifespan.py` spawns ~26 asyncio loops (CLAUDE.md's "16" is stale) inside the request process. Reconciliation, T4A batch, retention purge, Zoho sync, payment/push retry all compete for the same event loop and 64-thread pool as live dispatch. **Next step (no new infra):** split the non-latency-critical loops into a second Fly process group gated by `ROLE=worker` running the same codebase; keep dispatch/offer-expiry/surge/safety-checkin hot. This is the answer *before* you ever need Kafka.

### Observability
`sentry-sdk` is present; metrics are a hand-rolled Prometheus text exposition (`utils/metrics.py` — keep it, it's the source of truth). **No `opentelemetry-*`, no distributed tracing** — a cross-replica latency problem across ~26 loops + Redis WS fan-out is currently a black box. **Add** `opentelemetry-instrumentation-fastapi`/`-httpx` reusing the existing `X-Request-ID` as trace parent, export to Sentry (it ingests OTLP). Do it alongside the worker split.

### Watchdog gap (reliability)
`_WATCHDOG_LOOP_NAMES` (`lifespan.py:434-454`) omits `safety_checkin (30s)`, `driver_claim_reaper (60s)`, `preauth_capture`, `referral_payout`, `route_finalizer`, `reconciliation`, and more. `_restartable` recovers *crashes* but not *hangs* — a loop blocked in an un-timed `await` (hung DB/Redis) silently stalls with no alert. A stalled `safety_checkin` (the ≥20-min in-progress trip escalation) or `driver_claim_reaper` (silent supply erosion) is invisible to ops. **Fix:** add safety/finance-critical loops to the watchdog and ensure they `record_heartbeat`.

### Documentation drift (operational risk)
`README.md` still describes a `frontend/` dir, a Mongo-like `db.py`, and Railway-only deploy; `ARCHITECTURE.md:151+` documents a `develop → test Railway` staging/promotion flow — but `ACTION_ITEMS E1` states plainly **no staging exists** and deploys go `main → prod`. A new SRE would trust a QA gate that isn't there. **Fix:** reconcile the docs to reality; it's a real-incident risk for low effort.

### Known-and-correctly-tracked (validate, don't re-litigate)
Staging env (E1), load/marketplace sim (E2, harness built/unexecuted), forced-upgrade gate (E3), synthetic monitoring + SLO alerting (E4), feature-flag kill switches (E5), DAST + pentest (E6), backup-restore drill (E7), CODEOWNERS (E8), PostGIS surge count (D1), distributed tracing (D2). This is the right list, in roughly the right order.

---

## 🛠️ Maintainability & Code Smells

- **Two coexisting dispatch models.** Legacy single-offer (`assign_driver_to_ride` → `driver_assigned`, `_offer_timeout_handler` guarding `DRIVER_ASSIGNED`) vs. the live batch path (ride stays `searching` while offers pend, never sets `driver_assigned`). The single-offer timeout handler is effectively dead against batch rides — a future caller mixing the two gets a ride whose timeout never fires. **Confirm the single-offer path is retired and delete it.**
- **Fail-open vs fail-closed divergence on the same gate.** `dispatch_service.py:423-431` (`find_candidate_drivers`) fails **open** on the subscription gate (dispatches unfiltered on a DB blip), while the live `matching.py:439-445` fails **closed**. Reconcile to fail-closed (policy/revenue bypass otherwise).
- **`claim_driver` truthiness heuristic** (`dispatch_service.py:494`) mis-reads an empty-dict `{}` success as failure; latent because the live path uses `claim_driver_atomic`.
- **Float leakage into corporate money RPCs** (`payment_service.py:423,431,437,446` pass `_f(...)` floats). Round-trips today via `Decimal(str(...))`, but violates the Decimal-only rule and is one refactor from drift — both services already accept `Decimal`, so pass it through.
- **Two authoritative "owed" definitions disagree on tip.** `confirm_payment` (`payments.py:447-450`) derives owed from `grand_total` only; the webhook path (`webhooks.py:594-598`) uses `grand_total + tip`. Not exploitable today, but the settle paths should share one helper.
- **Zero-planned-distance fare fallback ignores area rate** (`fare_service.py:333-343`) — settles at the global default per-km, mis-pricing rides that complete with a missing planned distance (logged ERROR, but the wrong fare still settles).

---

## 🧪 Testing & QA (Missing Edge Cases)

Footprint is strong: **358 backend + 260 frontend test files, 21 CI workflows** (including `security-gates`, `migration-check`, `pip-compile-check`, `claude-audit`). Note the **global `--cov-fail-under=60`** (`backend/pytest.ini:15`) is *below* the per-domain minimums CLAUDE.md mandates (payments/fare/crypto ≥90%, rides/dispatch ≥80%) — the gate doesn't enforce the policy. Missing regression coverage implied by the findings above:

- **Rating idempotency (C1):** a retried `rate_driver` with a tip must not double-charge — add to `test_ride_state_machine.py`.
- **Wallet-pay tax branch (C2):** `/wallet/pay` on a ride with GST/PST + area fees must debit `grand_total`, not `total_fare`.
- **Email-OTP lockout (C3):** N wrong guesses → lockout + OTP invalidation (mirror the phone-OTP test).
- **Insurance-period guard (H4):** loser/decline while offline must NOT append a Period-1 row.
- **Earnings snapshot parity:** rider-completed vs driver-completed on a minimum-fare-clamped ride must produce the same driver-earnings figure (see below).
- **Partial-refund ledger (F2):** two partial `charge.refunded` events must not double-count against the GST base (record incremental, or dedup on the Stripe refund id — `webhooks.py:848-849`).
- **Corporate settle replay (F5):** a crash after money moves must not re-debit the company on the next sweep (pass `ride_id` as RPC idempotency key).
- **CI:** raise the coverage floor to the per-domain policy, and (once staging exists, E1) execute the built-but-idle Locust harness (E2) + a chaos step that kills a replica mid-dispatch to exercise the offer-expiry reaper and stuck-ride sweeper.

*Additional integrity bug worth a test now:* **rider-initiated completion under-reports driver earnings.** `routes/rides/lifecycle.py:246` feeds `base+distance+time` to `build_earnings_snapshot`, while the driver-completion path uses `fare_share(total_fare, …)` so a minimum-fare-clamped ride books the full uplift (driver keeps it at 0% commission). Same ride, two different earnings figures depending on who tapped "end" → T4A/payout drift. **Fix:** use `fare_share(...)` in `rider_complete_ride`.

---

## Product gaps vs Uber/Lyft — and the guardrails that rule some out

**Safe & high-value (mostly already built):**
- **Driver destination mode** — the team's self-declared #1 retention gap (D3), but the **matching algorithm already exists** (`dispatch_service.py:120 _ride_brings_driver_closer_to_destination`; `matching.py` honors `destination_mode`/`destination_lat/lng`). What's missing is only the **driver-app UI + a set-mode endpoint.** Far closer to done than D3 implies; fully guardrail-safe.
- **Driver heatmap** (D4) — `utils/demand_forecast.py` already produces hour×day forecasts server-side; surface read-only.
- **Upfront guaranteed price lock** — you already show fare pre-booking and cap surge; formalizing "price won't change" *reinforces* the anti-surge brand.
- **Forced-upgrade gate** (E3) — old binaries on changed APIs is the #1 silent rider-experience killer; cheap now, impossible to retrofit.

**Would VIOLATE Spinr's guardrails — flag, do NOT build:**
- Uber-style **driver Quests / streak / consecutive-trip bonuses** → control-of-work + SK contractor-misclassification risk ("Not a driver-control platform"). If pursued, use flat referral/loyalty rewards through legal review.
- **Personalized / willingness-to-pay dynamic pricing** → violates both "Not surge-first" (2.5× cap, visible pre-booking) and "Not a data-harvesting product."
- Any **"service fee"** to lift margin → violates "Not a hidden-fee operator" and the 0%-commission promise.
- **Third-party ad/attribution SDKs** → violates "Not a data-harvesting product" + PIPEDA data-minimization.

---

## Recommended plan (priority order)

**P0 — before scaled launch (exploitable / regulatory):**
1. C1 — rating idempotency CAS (money) — `rating.py`
2. C2 — `/wallet/pay` charge `grand_total` incl. tax (regulatory) — `wallet.py`
3. C3 — corporate email-OTP per-account lockout (account takeover) — `auth.py`
4. H4 — insurance-period `is_available` guard on loser/decline (regulatory audit) — `ride_flow.py`
5. Earnings-snapshot parity in rider-completion (payout/T4A) — `lifecycle.py`

**P1 — correctness & abuse hardening:**
6. F2 partial-refund ledger double-count · F3 Stripe PII leak · F5 corporate settle replay · F4 Stripe `api_version` pin on top-up · email-OTP send cap
7. Rider WS heartbeat watchdog (frozen driver map) · WCAG label pass on `ride-status.tsx`
8. Watchdog coverage for safety/finance loops
9. Doc drift fix (README/ARCHITECTURE vs. no-staging reality)

**P1 — architecture (additive, no rewrite):**
10. Route dispatch candidate search through the existing PostGIS radius RPC; drop the 500-row cap
11. `asyncpg` + transaction pooler for hot DB paths (lift the 64-thread ceiling)
12. `ROLE=worker` process split for non-latency loops
13. Wire driver destination-mode UI/endpoint (algorithm already exists)

**P2 — platform maturity (mostly already tracked in ACTION_ITEMS):**
14. OpenTelemetry tracing · admin analytics + app-settings caching · surge in-memory bucketing
15. Stand up staging (E1) → unblock load test (E2) + smoke (A2) + DAST (E6); feature-flag kill switches (E5); synthetic monitoring + SLO alerting (E4)
16. Raise CI coverage floor to per-domain policy

---

*Prepared as a read-only review. No code was modified. Every finding is file:line-anchored and was verified against the actual code path (including backing SQL RPCs) during the review.*
