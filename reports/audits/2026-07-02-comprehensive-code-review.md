# Spinr — Comprehensive Engineering Review (2026-07-02)

_Read-only teardown across backend, client surfaces, testing/CI, security, and money paths. Benchmarked against Uber/Lyft where relevant. No code was modified. Findings synthesized from five parallel specialist reviews._

**Headline:** This is an unusually well-hardened, audit-mature codebase — the hard problems (JWT trust model, OTP hashing, refresh-token rotation, Stripe idempotency, surge cap, insurance-period audit, atomic ride-accept CAS) are mostly _right_ and documented with rationale. The real risk is now concentrated in a **small number of state-machine/money edges that slipped past the atomic-update discipline applied everywhere else**, plus an **enforcement layer that lies** (a 60% coverage floor standing in for mandated 90%). Nothing here is a rewrite; it's a targeted hardening pass.

---

## 🚨 Critical Issues & Security Flaws

**C1 — `cancel_scheduled_ride` can cancel an in-progress ride** (`backend/routes/rides.py:5490-5513`). The only guard is `status in terminal_statuses()`; `is_scheduled` stays true after the scheduler flips the ride live, so an `in_progress` scheduled ride passes. The write is id-only (TOCTOU) and does no driver release, no insurance-period transition, no fee, no WS event. A driver keeps driving a ride the DB says is `cancelled`; settlement breaks. **Fix:** atomic `update_one` filtered to pre-trip states, 409 on zero rows, reuse the rider-cancel cleanup path at `rides.py:4596-4906`. Directly violates the "cancelled only before in_progress / every transition emits WS" invariants in CLAUDE.md.

**C2 — Go-online sets `is_available=True` while the driver is mid-trip** (`backend/routes/drivers.py:6525`). `is_available` is set to `is_online` unconditionally; the active-ride guard only runs on the offline path. A reconnect/re-tap re-asserts availability → a **second concurrent ride is offered to a busy driver**. The invariant assert at `:6537` compares a value to itself and can never fire. **Fix:** compute `is_available = is_online AND no active ride AND no pending offer` on the online path too. Violates the `is_available ⇒ not-on-a-ride` invariant.

**C3 — Money-mutating Postgres functions lack `SECURITY DEFINER` + pinned `search_path`** (`backend/migrations/28_corporate_wallet_rpc.sql:3`, `29_corporate_allowance_rpc.sql:13`). Both move real corporate money yet violate the project's own convention that every money function be `SECURITY DEFINER SET search_path = ...` — sibling migrations 50 and 196 do it correctly. **Impact:** search_path-hijack surface against functions that move money. **Fix:** `CREATE OR REPLACE ... SECURITY DEFINER SET search_path = public, pg_catalog` in a new migration (next free slot is **202**, not 145 — CLAUDE.md is stale).

**C4 — WebSocket auth silently auto-creates users and swallows DB outages** (`backend/routes/websocket.py:414-459`). On a missing `users` row the WS path calls `create_user(...)` — the exact "fall through to create user → phantom/duplicate account" anti-pattern CLAUDE.md forbids and that the HTTP path (`dependencies/__init__.py:383-397`) deliberately refuses. A blanket `except Exception` also turns a Supabase blip into `invalid_token`, and a Firebase UID whose real row was soft-deleted can re-provision an active account over a socket, bypassing the PIPEDA deletion flow. **Fix:** mirror `get_current_user` — never create on miss; close 1013 on DB error with `logger.error`.

**C5 — `ride_status_update` WS message lets any authenticated user spoof any ride's status** (`backend/routes/websocket.py:946-967`). No participant check (contrast `chat_message` at `:1035`), no state validation; the client string is relayed verbatim as `ride_status_changed` to the victim's rider and all admins. **Fix:** verify sender is the assigned driver/rider, validate against the canonical state set, echo the DB status.

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**The convention "do not silently swallow DB/auth/payment/dispatch errors" is violated in several hot paths:**

- **Masked dispatch outage** (`repositories/driver_repo.py:224-231`, `:100-104`): `match_and_claim_driver` does `except Exception → return None`, so a Supabase outage is indistinguishable from "no drivers." Rides spin until auto-cancel and match-rate KPI craters silently. **Re-raise `DatabaseError`; alert.**
- **Earnings/active-ride endpoints return `[]`/$0 on DB failure** (`routes/drivers.py:1342, 1475, 1568, 1612, 1091`; `:3629`): a failed read renders "you earned $0" or drops a pending-offer UI instead of a retryable 503.
- **Raw exception text to clients** (`routes/documents.py:313, 945, 959` — `detail=f"...: {e}"` leaks storage internals; `rides.py:4824-4831` leaks "Check backend logs for [CANCEL] lines" to the end user). Bypasses the sanitization layer at `utils/error_handling.py:719`.
- **PII in logs (PIPEDA-forbidden)** — one-line deletes: `repositories/_base.py:628-652` logs full `drivers` UPDATE payload **and returned row** (encrypted address, license, phone, lat/lng) at INFO; `routes/rides.py:1707-1761, 2525-2580, 606-608` print raw pickup/dropoff lat/lng at ~1m precision.
- **Sentry `domain` tag largely missing** — the init in `server.py:403-470` is genuinely excellent (PII scrub regex, `send_default_pii=False`, allowlisted ID-only tags), but only ~112 call sites attach `extra={...}`, so most captured events can't be triaged by domain (`dispatch`/`payments`/`safety`/…).
- **Client-side raw errors reach users**: admin `error.tsx:14` renders raw `error.message`; `driver-app/app/become-driver.tsx:270,495` does `Alert.alert(err.message)`. (Mobile ErrorBoundary/ErrorScreen correctly gate stacks behind `__DEV__` — good.)

**Done well:** the `run_sync` resilience stack (`repositories/_base.py`) — half-open circuit breaker with single-probe anti-thundering-herd, global Redis retry budget, full-jitter backoff, deadline-aware retry skipping, and a `DatabaseError` that preserves `details["original"]` exactly per convention. The shared client's error normalization (`shared/api/client.ts:263-618`) collapses four backend error shapes into one i18n-keyed `ExtractedError` with request-ID correlation and a GPS-redacting error ring buffer.

---

## 🐢 Performance Bottlenecks & Optimizations

Benchmarked against the CLAUDE.md SLA table (dispatch <2s, fare estimate <300ms, settlement <1s, WS fan-out <100ms, location write <150ms).

- **Booking blocks on the whole dispatch pipeline** (`routes/rides.py:2838, 3196, 3223`): `snap_to_road`, Directions (~3s), and `await match_driver_to_ride` (driver scan + Redis + Distance Matrix ETA + offers insert + per-driver quest N+1) all run **inside** the create-ride handler. **Fix:** `create_task(match_driver_to_ride(...))` after insert.
- **WS unicast has no send timeout** (`socket_manager.py:290-304` + `utils/ws_pubsub.py:311-389`): one half-closed client blocks `send_json` for tens of seconds; every ride offer / `ride_taken` / status change on that replica queues behind it. Broadcasts got the 2s-timeout fix (B-P3-1); unicast did not. Breaches both the <100ms and <2s SLAs from a single bad client. **Fix:** `asyncio.wait_for` in `_deliver_local` / per-connection send queues.
- **Settlement awaits a road-snap provider inline** (`routes/drivers.py:4772`): `complete_ride` awaits `compute_road_route` (OSRM/Google over up to 10k breadcrumbs) before settling, vs the <1s SLA. The pickup-leg snap was deliberately backgrounded; this one wasn't. **Fix:** settle on haversine, reconcile snapped distance in the background.
- **`GET /drivers/leaderboard` is N+1 over the fleet** (`routes/drivers.py:5998-6081`): ≤500 drivers × (rides query + user lookup) → ~1,000 sequential queries per driver-app screen open; exhausts the 64-thread pool. **Fix:** use the existing `driver_daily_stats` aggregate.
- **Blocking Supabase call on the event loop** (`routes/rides.py:3745-3751`): `.execute()` called without `run_sync` in `get_ride` freezes every concurrent request.
- **Inline external awaits on SLA'd transitions**: FCM in accept (`drivers.py:4259`), receipt email in `process_payment` (`rides.py:4162`), 5 serial Twilio SMS in SOS (`rides.py:5215-5229`), sync Stripe SDK on the loop thread (payouts/subscribe). Siblings (arrive/verify/start) correctly use `create_task` — these are the stragglers.
- **`accept_ride` / `compute_ride_estimates` sequential round-trips** (~7 each) inside 2s / 300ms budgets — `asyncio.gather` them.
- **Durable `drivers` UPDATE per 1Hz GPS ping** (`websocket.py:678`) — write amplification vs the 150ms SLA; also the replay outbox sequences GPS pings, evicting durable ride events (see below).
- **Client GPS/breadcrumb data loss**: `driver-app/hooks/useDriverDashboard.ts:528-535` deletes up to 500 breadcrumbs (billing + SGI audit data) after ~90s of upload failure; `utils/backgroundLocation.ts:144-156` drops background batches with no retry/persistence.

**Done well:** dispatch pushes already moved off the request path; estimate polyline overlapped with fare work; partial recency index for the estimate driver page; instant-payout idempotency choreography; the driver-app WS client (auth-gated connected state, `?last_seq` resume + active-ride refetch, jittered backoff).

---

## 💡 Tech Stack & Architecture Recommendations

The stack itself is current and appropriate (FastAPI + Supabase/Postgres + Redis + Stripe + Expo SDK 55/RN 0.85/React 19 + Next 16 — note CLAUDE.md's "Expo SDK 54" is stale). Gaps are operational maturity vs. Uber/Lyft, not framework choices:

1. **DB write-retry policy is inverted** (`repositories/_base.py`): `insert_one`/`update_one`/`delete_many`/`rpc` all retry under `retry_policy="read"` — a commit-then-lost-response retries and **double-writes** (`create_flag` → 3 flags auto-ban; `increment_promo_uses` double-count; `claim_ride_atomic` retried-after-commit tells a driver "ride taken" on their own ride). Wallet RPCs are internally idempotent; the rest aren't. **Default writes/rpc to `retry_policy="write"`.**
2. **No staging environment** (E1) — deploys go `main` → prod on Fly + Railway with nothing in between. This is the single biggest ops gap and blocks load-test execution (E2), safe migration rehearsal, and DAST (E6). **Stand up a Fly staging app + throwaway Supabase.**
3. **No synthetic monitoring / alerts-as-code** (E4) — `docs/slo.md` defines thresholds as prose with nothing consuming them; no Grafana/Alertmanager/Datadog config in the repo; a total outage is discovered by users. The `/metrics` spine is excellent and the metric names match the contract — it just needs an external prober (Checkly/UptimeRobot) + alert rules on /health, auth, fare-estimate.
4. **Split the `routes/drivers.py` god-file** (8,468 lines, 68 endpoints, 9 domains): subscription-gate logic is triplicated and already drifting (`:3966`, `:6365`, `:6943`); driver-matching is reimplemented 3× vs `services/dispatch_service.py`; payout/Stripe money logic is route-embedded with no service layer, making the ≥90% money-coverage mandate untestable. Extract subscriptions → payouts/banking/tax → ride lifecycle into services.
5. **WS per-user rate limit is per-replica** (B4, `socket_manager.py:27-37`) — promote the counter to Redis `INCR`/`EXPIRE` with in-process fallback.
6. **Kill switches / feature flags** (E5) — no documented per-subsystem kill switch (surge, scheduled dispatch, promo, corporate billing); add boolean gates checked at the top of each loop + admin toggles so a misbehaving subsystem can be disabled without a deploy.

---

## 🛠️ Maintainability & Code Smells

- **Dead code that is a trap for the next caller**: `shared/api/cachedClient.ts` (broken auth — reads a SecureStore key `authStore` deletes; 3 files/3 storage strategies), `shared/api/offlineQueue.ts` + `rideStore.syncOfflineRequests` (two dead half-implementations of the offline queue; the live-ish replay at `rideStore.ts:560` omits the idempotency key → double-book risk if ever wired), `backend/utils/receipt_email.py` (dead, hardcodes PST that SK rideshare doesn't charge — dangerously named vs the live `email_receipt.py`), dead axios clients in both apps' `utils/apiClient.ts` (cookie-auth model contradicting the bearer design; explains the stale "Axios interceptor" note in CLAUDE.md).
- **Documentation drift**: CLAUDE.md says next migration slot 145 (actual **202**), "16 background loops" (actual **23**), "Expo SDK 54" (actual 55); ACTION_ITEMS A2/E10/E11 are marked open but are implemented or half-implemented (post-deploy smoke test exists at `ci.yml:610`; Python license gate is live; axe is wired for admin).
- **Watchdog blind spots** (`core/lifespan.py:403-422`): registers 16 loop names but 23 spawn — `preauth_capture`, `referral_payout`, `safety_checkin`, `reconciliation`, `suspension_reactivation` and others are unwatched (money/safety-critical); conversely `t4a_annual_job` is watched but never heartbeats → perpetual false stale-alert.
- **Float on money in display endpoints** (`drivers.py:1284-1289, 3899-3931`; `rides.py:3760-3789`) and Decimal→`float()`→`str()` round-trips into corporate RPCs (`payment_service.py:284, 293`) — latent, works today only because amounts are 2dp-dyadic.
- **The pre-commit float-money guard doesn't block** — it only warns and isn't scoped to the two named files, so the documented "hook blocks float arithmetic" is advisory-only; enforcement rests on review.

---

## 🧪 Testing & QA (Missing Edge Cases)

- **Coverage floors are unenforced (P0, ACTION_ITEMS A1)**: `backend/pytest.ini:11` sets a 60% global floor; there is **no per-module enforcement anywhere**. A PR can drop `routes/payments.py` to 0% and CI stays green. The CLAUDE.md 90%/80% money-path mandate is aspirational. **Fix:** per-path `coverage report --fail-under` (payments/fare/crypto ≥90, rides/dispatch ≥80).
- **`calculate_fare()` / `recalculate_fare_for_distance()` have zero direct unit tests** (`services/fare_service.py:185, 288`) — the core money function is only covered indirectly via receipt rendering.
- **Untested Stripe webhooks that move money**: `charge.dispute.created` (`webhooks.py:774`), `charge.dispute.closed` (`:850`), `account.updated` (`:978`), and no behavioral test for the `charge.refunded` success handler (`:721`). Idempotency itself _is_ tested.
- **Dispute refund on wallet/corporate-paid rides silently no-ops while telling the rider money was returned** — see P1 in money section below; untested path.
- **Payment-retry exhaustion** (`utils/payment_retry.py:80,126`) and **WS mid-trip missed-event resync** are untested (`test_p1_ws_reconnect.py:5` admits messages during disconnect are "permanently lost").
- **Shared `@spinr/shared` tests are orphaned** — no `test` script/runner in `shared/package.json`; the auth/refresh interceptor used by both apps is ungated; driver-app mocks `@shared` wholesale.
- **Mobile e2e suites never run in CI** (5 rider + 7 driver Playwright specs); 44 rider + 34 driver screens have no automated gate. Admin e2e is all API-mocked and asserts only `main` visibility with `.catch(()=>{})` swallowing failures.
- **SAST findings never fail builds** — Bandit/Semgrep/ESLint-security/gitleaks all `|| true`/`continue-on-error`; no DAST anywhere (E6).

**Done exceptionally well:** surge-tier boundary tests pin every ratio and the 2.5× cap exactly; concurrency discipline (atomic ride-claim race, offer timeout, refresh-token reuse cascade, DST-boundary scheduling, insurance-period transitions) all have dedicated tests; migration hygiene tooling hard-fails on edited-merged-migration / missing-RLS-on-CREATE / missing-rollback-comment; the `loadtest/locustfile.py` harness is a realistic two-sided marketplace with self-failing SLA gates — ready the day staging exists.

---

## 💰 Money-Path Findings (payments, wallet, corporate, refunds)

**P1 — Dispute refunds on wallet/corporate-paid rides silently no-op while the rider is told the refund was issued** (`routes/disputes.py:220-296`). `admin_resolve_dispute` only issues a real refund when a Stripe `payment_intent_id` exists. Wallet- and corporate-paid rides have none by design, so the code logs "manual required" but then unconditionally sets `status="resolved"`, writes `refund_amount`, and pushes the rider *"A refund of $X has been issued"* — while no `wallet_apply_credit` / `corporate_*` reversal / Stripe call ever runs. **This is the single largest live money-loss vector found.** Untested. **Fix:** branch on payment method to the correct reversal before resolving; keep status distinct (`pending_manual_refund`) and don't notify until the manual step completes.

**P1 — `add_tip` credits driver earnings + T4A with money that may never be collected** (`routes/rides.py:3805-3855`). No charge is initiated; capture only covers card rides with an open hold inside the 20-min window. A tip on an already-paid or wallet ride inflates `driver_earnings` with zero rider debit; the one-tip guard is read-then-write (concurrent tips both pass). **Fix:** settle the tip in the same flow; enforce uniqueness with `UPDATE ... WHERE tip_amount = 0`.

**P1 — Non-atomic wallet mutations (lost-update races)**: rider-cancel fee (`rides.py:4645-4673`), no-show fee (`drivers.py:5563-5594`), and driver cancellation-fee payout (`cancellation_service.py:122-135`) all do read-balance → compute → id-filtered update, bypassing the locked `wallet_apply_*` RPC pattern the corporate layer mandates. Also: when the clamp fires or the card charge fails, the ride still records/pays the **full** fee — recorded ≠ collected. Note commit `28eda66` intentionally pays the driver cancellation fee even when the rider's card declines — an unbounded liability worth a finance re-confirm.

**Verified clean and genuinely strong:** Decimal end-to-end in `calculate_fare`; surge multiplies only distance+time and is corporate-exempt (with a regression suite); 2.5× auto cap + justified manual override; `claim_stripe_event` before every dispatch; deterministic (not random-UUID) Stripe idempotency keys; server-authoritative charge amounts (never trusts client `amount`); `financial_events` ledger written before the ride-row update (crash-safe); allowance→master-wallet fallback capped correctly; `fare_split` remainder handled with `ROUND_DOWN` + requester absorbs the cent.

---

## 📈 Manager's Verdict — Overall Code Health

**Grade: B+ / strong pre-launch, with a short list of must-fix edges.** This codebase is well above the median for a pre-launch rideshare platform and, in several areas (auth hardening, money-safety choreography, migration tooling, the DB resilience stack, surge/insurance-period rigor), is at or near Uber/Lyft-tier discipline. The team clearly audits itself — most classic vulnerabilities are already closed *and documented with the reasoning*.

The residual risk is **not** architectural; it's a handful of state-machine and money edges that escaped the otherwise-consistent atomic-update discipline, plus operational maturity gaps (no staging, no synthetic monitoring, unenforced coverage floors) that are normal to hit at this stage but must close before a public launch on a payments+PII platform.

**Where it trails Uber/Lyft:** no staging/canary, no external synthetic monitoring or alerts-as-code, no kill switches, no destination-mode/heatmap driver-retention features (P3 backlog), and a coverage-enforcement layer that reports 60% where 90% is mandated.

**Fix-first shortlist (order):**
1. C1 scheduled-cancel guard + C2 go-online availability (concurrent-ride / phantom-cancel corruption) — atomic filters.
2. Invert the DB write-retry policy (`_base.py`) — silent double-writes today.
3. WS unicast send timeout (`socket_manager.py`) — one bad client stalls dispatch fleet-wide.
4. Dispute wallet/corporate refund no-op (`disputes.py`) — live money loss + false "refunded" notice.
5. C3 SECURITY DEFINER on the two corporate money RPCs.
6. Delete the 9 PII log lines (one-liners) + re-raise the masked dispatch outage.
7. Then: per-module coverage floors (A1) and stand up staging (E1), which unblocks load/DAST/monitoring.

Everything else is P2/P3 hardening and doc/code drift — worth a cleanup sprint, not launch-gating.
