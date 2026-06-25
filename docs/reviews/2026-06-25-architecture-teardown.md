# Spinr — Engineering Teardown & Remediation Plan

> **Read-only architectural review.** No code was modified. Produced 2026-06-25 by a
> six-stream parallel audit (security, money/payments, performance, error-handling/telemetry,
> tech-stack/architecture, testing/QA) over the backend at HEAD, with market comparison to
> Uber/Lyft. Findings marked **⊕ corroborated** were independently flagged by more than one
> review stream — treat those as high-confidence.

---

## Executive summary

Spinr is **above-average for its stage**. The engineering hygiene is genuinely strong: a
sanitizing global error handler that never leaks stack traces to clients, request-ID
correlation, a DB circuit breaker with half-open recovery, replay-safe background loops with
atomic claims / Redis leader locks, comprehensive production-boot validation, correct Stripe
signature + idempotency gating, and a real (269-file) backend test suite with meaningful
assertions. The stack is current to the point of being bleeding-edge.

The platform is **not gated by code quality**. It is gated by three things: (1) a cluster of
**money-precision and receipt-transparency defects** that violate the project's own Decimal-only
and line-item rules and carry regulatory weight; (2) a handful of **PII-to-third-party / PII-in-
channel leaks** (Stripe, FCM, JWT) that are PIPEDA exposures; and (3) **structural scaling
ceilings** (REST-over-HTTP DB access, 21 in-process polling loops, no distributed tracing) that
are fine at Saskatchewan launch scale and become the dominant constraints in that order.

**Verdict: FIX BLOCKERS before public launch.** None require a rewrite; most are surgical.

---

## 🚨 Critical Issues & Security Flaws

### C1 — `confirm_payment` returns success-shaped response when Stripe key is missing
`routes/payments.py:448` returns `{"status": "unknown", "mock": True}` *after* the atomic
`claim_ride_payment_processing` claim when `stripe_secret` is empty. A transient empty value from
`get_app_settings()` during key rotation confirms rides as processed with **no real charge**.
**Why it's critical:** silent revenue loss; the `mock` flag is client-visible but not enforced
server-side. **Fix:** raise HTTP 503 unconditionally when the processor is absent (mirror
`create_payment_intent` at `:161`). Never return a success shape when the payment processor is down.

### C2 — Silent user creation on the JWT fallback path ⊕ corroborated (security + error-handling)
`dependencies/__init__.py:405–418` (and the equivalent at `auth.py` flagged separately): when a
valid JWT resolves but `get_user_by_id` returns `None`, the code **creates a new `rider` row**.
A 404 is ambiguous — a brief Supabase replica outage forks a phantom account with a new UUID.
This is the exact "generic fallback that hides the symptom → duplicate accounts" anti-pattern
CLAUDE.md prohibits. **Fix:** raise 503 on `None` (same as the `DatabaseError` path); user
creation belongs only in `/auth/verify-otp` and `/auth/firebase`.

### C3 — OTP DB error swallowed and counted as a wrong code
`routes/auth.py:390–393`: if `get_otp_record_by_phone` raises, the code logs ERROR then falls
through to `otp_record = None`, which **increments the failure counter and can trigger a
24-hour lockout** — locking out a user who entered the *correct* code during a DB blip.
**Fix:** re-raise a 503 from inside the `except` before the counter increment.

### C4 — Full name + email transmitted to Stripe at customer creation (PIPEDA)
`routes/payments.py:125–128` passes `email` and concatenated full legal `name` to
`stripe.Customer.create`. Neither is required (only `metadata.user_id` is). This is an
unnecessary cross-border PII transfer to a US processor. **Fix:** send only
`metadata={"user_id": ...}` + idempotency key; attach email only if/when a dispute event needs it.

### C5 — Rider's real first name in driver-visible WS + FCM payloads (PIPEDA)
`routes/rides.py:3770/3776/3785` (`tip_received`), `:1175` (`new_ride_assignment`, **pre-
acceptance**), `:4160` (ride-share). FCM data payloads are cleartext in the Android tray and stored
in Google infra with no Canadian-residency guarantee; CLAUDE.md bans full names outside `user_id`.
Uber/Lyft show "Your passenger," not the legal name. **Fix:** use an alias / first-initial, or
omit and correlate by `rider_id`.

### C6 — Double-credit window on wallet top-up / refund webhooks
`routes/webhooks.py:508–512` (`wallet_topup`) and `:703` (`charge.refunded`): the
`wallet_increment_balance` + `wallet_transactions` insert has **no unique constraint or dedup on
`reference_id`**. A crash between the wallet write and `mark_stripe_event_processed`, followed by a
Stripe retry, can double-credit. **Fix:** route through a `SECURITY DEFINER` Postgres function with
an idempotency check on `reference_id` (mirror `corporate_wallet_apply_delta`), or add a unique
index on `wallet_transactions.reference_id`.

### C7 — Break-glass super-admin token cannot be revoked
`routes/admin/auth.py:1258–1276`: the 1-hour `super_admin` JWT mints a JTI but **never registers
it in the `admin:revoked:*` denylist**, so `/admin/auth/logout` can't kill it. A leaked token
(e.g. pasted into Slack) is only mitigable by rotating `JWT_SECRET` — which kills every session.
**Fix:** register the JTI on mint with a matching TTL; delete to revoke; log the JTI in the audit row.

**Lower-severity security (WARNINGS):** phone number embedded as a JWT claim
(`dependencies/__init__.py:105`) — decodable in any log; `localhost:3000/3001` permanently in the
**production** CORS allowlist (`core/middleware.py:515`); admin-login account-existence oracle via
401-vs-423 (`admin/auth.py:322`); 4-digit OTP (Uber/Lyft use 6) whose safety rests entirely on
rate-limiting; `admin-001` refresh path omits `audit`/`support_tickets` modules vs `ALL_MODULES`
(`admin/auth.py:466`); card brand+last4 logged at INFO (`payments.py:712`, PCI-DSS scope creep).

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

**The infrastructure is excellent and should be the template:** `utils/error_handling.py`
sanitizes 5xx to an `ERR_*` sentinel allowlist (no stack-trace leak), correlates `request_id`
between body and logs, logs `e.details["original"]` for DatabaseErrors, and uses `opt(raw=True)`
so handlers can't crash on `{`-bearing tracebacks. No `print()` in request paths, no bare
`except:` in production, Sentry scrubbing and metric naming compliant. The gaps are where this
discipline isn't applied:

- **E1 (HIGH) ⊕ corroborated (perf + error-handling):** dispatch presence filter at
  `routes/rides.py:742` catches **all** exceptions and silently dispatches to unfiltered,
  possibly-offline drivers at WARNING — masking real filter bugs, not just Redis outages — and
  **never emits `spinr_dispatch_presence_filter_failed_total`**, a metric CLAUDE.md explicitly
  mandates. Result: ghost-driver offers and long rider searches during a Redis blip, invisible to
  ops. **Fix:** narrow the catch to Redis exceptions for fail-open, log ERROR + re-raise on
  unexpected, and emit the counter.
- **E2 (HIGH):** payment-failure push to rider logged at **DEBUG** (`services/payment_service.py:728,
  772`) and subscription-cancel push at DEBUG (`webhooks.py:958`). During an FCM outage the rider is
  never told the ride/charge failed — a user-facing payment state change that must be ERROR.
- **E3 (MEDIUM):** money/pricing tick-loops log without `exc_info=True` (`surge_engine.py:366`,
  `scheduled_rides.py:366`, `corporate_autotopup.py:163`) — stack lost on billing failures.
- **E4 (MEDIUM):** no per-loop failure metric on reconciliation / push-retry / referral-payout /
  T4A / allowance-reset; a stuck money/regulatory loop is undetectable. Standardize a
  `spinr_bgloop_errors_total{loop=...}` counter.
- **E5 (MEDIUM):** retention-purge telemetry is a `pass` stub (`utils/retention_purge.py`) — the
  PIPEDA-mandated PII-purge loop runs with **zero observability**; wire it to `utils/metrics.py`.
- **E6 (MEDIUM):** admin `logout-all` WS-kick failure at WARNING ⊕ corroborated (security +
  error-handling) — a compromised admin's socket keeps streaming for the 30s heartbeat window;
  must be ERROR with `exc_info` (the rider path already is).

**User-experience side is sound** (no raw errors leak); the deficit is **admin-side visibility**:
the most important failures (payment-notify, dispatch degrade, PII purge) are logged too quietly or
not metered, so ops can't see them.

---

## 🐢 Performance Bottlenecks & Optimizations

Ordered by impact against the CLAUDE.md SLAs.

1. **CRITICAL — surge engine re-scans the whole driver table per area, on every replica, with no
   leader lock.** `utils/surge_engine.py:160–195` does `get_rows("drivers", …, limit=5000)`
   *inside* the per-area loop + Python point-in-polygon → O(areas × drivers), and the loop has only
   ±10% jitter (`:368`), so all N replicas run it independently — a replay-safety contract
   violation on a revenue-critical loop. Bursts saturate the 64-thread DB executor and bleed
   latency into dispatch/fare/location. **Fix:** flip on the already-built spatial path
   (`_SURGE_SPATIAL_COUNT`, PostGIS `ST_Covers` + GiST, server-side count) and wrap
   `recalculate_all_surges` in a Redis `SET NX EX` leader lock. (This is how Uber/Lyft do geo-supply
   — index server-side, compute once, fan out.)
2. **CRITICAL — driver leaderboard N+1 over the fleet.** `routes/drivers.py:5349–5391` reads 500
   drivers then issues per-driver `rides` + `users` queries → ~1000 sequential round-trips, trivially
   DoS-able. **Fix:** one aggregating SQL/RPC (`GROUP BY driver_id` + `SUM` over the window), or at
   minimum batch with `.in_()` and push the date filter into the query.
3. **HIGH — per-driver quest query inside the <2s dispatch loop.** `routes/rides.py:1115–1142` awaits
   a `quest_progress` select per claimed driver (up to 10), serialized, delaying the WS offer. **Fix:**
   one `.in_(driver_uids)` batch before the loop, or send the offer first and enrich the (cosmetic)
   quest banner via a follow-up event.
4. **HIGH — dispatch claim + re-read is two serial round-trips per candidate**
   (`routes/rides.py:998–1012`); the `get_driver_by_id` cache was just invalidated by the claim.
   **Fix:** use the existing `match_and_claim_driver` RPC (`FOR UPDATE SKIP LOCKED`) or
   `returning=representation` on the claim UPDATE to get the row back in one call.
5. **HIGH — `update_location_batch` does 3–4 driver lookups per GPS ping**
   (`routes/drivers.py:1877/1903/1928`); the second read is redundant. **Fix:** read once, reuse the
   row, use `get_driver_by_user_id_cached`; align the REST path with the leaner WS path.
6. **MEDIUM — `httpx.AsyncClient` constructed per ETA call** (`utils/maps_eta.py:106/216/320`) — a
   fresh TLS handshake every call, ~50–150ms on the dispatch `batch_get_etas` path. **Fix:** one
   module-level pooled client at startup.
7. **MEDIUM — admin location fan-out is broadcast-to-all-admins** with no viewport filter
   (`socket_manager.py:356`, the code itself flags the follow-up) → O(drivers × admins) every 3s,
   threatening the `spinr_ws_fanout_duration_ms` <100ms SLA. **Fix:** admin bbox-subscription protocol
   (Uber's "god view" model) + parallelize `_deliver_broadcast_local` sends.
8. **LOW/MED — earnings/stats endpoints pull 5k–10k rows then sum in Python**
   (`routes/drivers.py:997…1302`). **Fix:** SQL aggregates + pagination.

**Already done right (not findings):** targeted WS unicast fan-out; batched presence skip-list
(`redis_mget`); WS location hot path (DB write un-gated, ETA cache-only with off-loop refresh,
buffered breadcrumbs); FCM offloaded via `create_task` in dispatch; circuit breaker with half-open
recovery.

---

## 💡 Tech Stack & Architecture Recommendations

The stack is current (FastAPI 0.136, Pydantic 2.13, React 19.2, RN 0.85, Expo 55, Next 16) and
well-disciplined. The ceiling is three structural choices, in priority order:

1. **Data access is REST-over-HTTP (PostgREST), not pooled Postgres.** Every query is a full HTTP
   request through a 64-thread `ThreadPoolExecutor` wrapping a *sync* httpx client; no PgBouncer in
   the request path; no real transactions/`FOR UPDATE` except via RPCs. This caps throughput and
   inflates P95. **Adopt** `asyncpg` + SQLAlchemy 2.0 async against **Supabase Supavisor/PgBouncer**
   (transaction mode, port 6543) for the hot paths (dispatch, fare, wallet); keep `supabase-py` for
   low-frequency RLS-convenient CRUD. Phased, not a rewrite.
2. **Job/event fabric is 21 in-process asyncio polling loops on every replica** (CLAUDE.md says 16 —
   stale). Polling = latency floor + baseline DB load that scales with replica count; no DLQ /
   backpressure / fan-out. **Near-term:** move cron-style loops (T4A, reconciliation, retention,
   allowance reset) to Fly scheduled machines / a single worker so they stop running redundantly.
   **Real fix:** a Redis-backed queue (**arq** or Celery 5) producing on ride-state transitions
   instead of sweeping; extract `dispatch_service.py` (already cleanly separated) into its own
   deployable worker. Kafka/Kinesis is premature.
3. **No distributed tracing.** Per-request `X-Trace-ID` exists but metrics are per-process and
   un-aggregated; no span propagation into Supabase/Stripe/Twilio. **Adopt** OpenTelemetry
   (`opentelemetry-instrumentation-fastapi`/`-httpx`/`-redis`) → Sentry tracing (already in stack;
   just enable `traces_sample_rate`) or Grafana Tempo; stand up real Prometheus+Grafana scraping
   `/metrics` from all machines (names are already Prometheus-idiomatic).

**Other gaps a market leader has:** feature flags / kill-switches (none — a payments+safety platform
should not ship matching/pricing changes without a gated rollout and an instant off-switch; **Unleash**
self-hosted for Canadian residency); Supabase **read replicas** for admin/analytics traffic; Redis
**Streams** (consumer groups + `XACK`) for dispatch-critical events instead of fire-and-forget pub/sub,
plus Redis HA (single Redis is a SPOF for pub/sub + cache + locks + rate-limit); automated
health-based failover (today's Fly↔Railway cutover is a manual Cloudflare CNAME swap, human-paced
RTO).

**Footprint / vendor sprawl (cheap reliability + PIPEDA win):** a **1 GB Fly VM** carries
`pandas`/`numpy`/`boto3`/`pyiceberg` (likely dead transitive weight) + three LLM SDKs
(`google-generativeai` is **deprecated** → `google-genai`) + 21 loops/process. Mobile carries
**three** overlapping crash/session vendors (LogRocket + Sentry + Crashlytics) — verify LogRocket
isn't capturing GPS/PII (CLAUDE.md forbids), consolidate. rider-app lacks `@tanstack/react-query`
that driver-app has; ESLint 8 (EOL) vs 9 drift; Railway runs `--workers 4` vs Fly `2` (config drift
between "identical" environments).

**CI/CD is a strength** (22 workflows, security gates, migration checks) — but integration tests use
a raw `postgres:15` container while prod uses Supabase REST, so **PostgREST/RLS behavior is never
exercised**, and there's no perf-regression gate despite `loadtest/` + `perf_baseline.py` existing.

---

## 🛠️ Maintainability & Code Smells

- **`routes/rides.py` is a 5,811-line god-file** (graphify confirms high centrality). It mixes
  dispatch, state machine, fare display, tips, ride-share, and PII formatting. Hard to test to the
  ≥80% floor, hard to review. **Plan:** carve dispatch, fare-breakdown, and notification-formatting
  into modules behind the existing service layer.
- **Two implementations of the same logic** invite drift: OTP hashing via `crypto.verify_otp_hash`
  vs an inline `hmac.compare_digest` in `auth.py:388`; fare-breakdown line building duplicated in
  `services/fare_service.py:242` and `routes/rides.py:491`. Consolidate to one.
- **Confusing money-service naming:** `settle_corporate` calls `apply_rollback` to *debit* an
  allowance (`payment_service.py:280`) and `apply_grant` to reverse it — the names read backwards.
  Rename to `apply_debit`/`apply_credit` or document the convention inline.
- **`admin_earnings` field stores the booking fee**, not a commission — correct, but the name
  invites a future contributor to treat it as `platform_share` and wrongly deduct from driver payout.
  Rename or comment.
- **Doc drift:** CLAUDE.md says Expo 54 / 16 loops; reality is Expo 55 / 21 loops. Keep the canonical
  doc honest — it's the contract every agent reads.

---

## 🧪 Testing & QA (Missing Edge Cases)

The suite is **real, not smoke** (269 backend files; verified strong coverage of the ride-acceptance
race, OTP lockout incl. fail-closed-on-Redis, all insurance-period transitions, surge-cap boundary,
corporate allowance split, refund/idempotency replay). Gaps:

- **CI gate under-enforces the spec.** `pytest.ini` sets a single global `--cov-fail-under=60`; there
  is **no per-file floor**, so payments/fare could silently regress 92%→70% and still pass. This is
  also ACTION_ITEMS A1, the largest open P0. **Fix:** per-path `coverage report --fail-under` (90 for
  payments/fare/crypto, 80 for rides/dispatch).
- **`charge.dispute.created` / `charge.dispute.closed` webhooks: ZERO tests.** They write
  `stripe_disputes`, flip `payment_status='disputed'`, and broadcast to admins. CLAUDE.md: every
  Stripe webhook type must have a test before prod. A chargeback regression ships silently.
- **`settle_corporate` master-wallet-failure saga: untested** (`payment_service.py:289–322`) — the
  branch where allowance was debited but the master-wallet fallback raises. The highest-value
  untested money branch; a regression leaves corporate ledgers inconsistent.
- **No property-based tests** on money math — and a cents-truncation bug *already happened*
  (`test_dispute_refund_cents.py`). Add Hypothesis over `dollars_to_cents`, `ratio_to_multiplier`
  (monotonic, never > 2.5), and "fare total == sum of line items."
- **No real integration/RLS tier and no load tests.** RLS policies (the P0 cross-user-exposure
  scenario) are never exercised because unit tests mock Supabase; SLAs (dispatch <2s, fan-out <100ms)
  are unguarded. The load harness exists (`loadtest/locustfile.py`) but is blocked on a staging env.
- **Untested admin modules** (`admin/safety.py` most concerning, plus documents/faqs/incentives/
  legal_documents/promotions/staff/support/venues) — add at least allowed+denied authz tests each.

---

## 📈 Manager's Verdict

**Health: B+ / launch-ready after a focused blocker sprint.** This is a disciplined codebase that
already does the hard, easy-to-skip things right (sanitized errors, idempotent webhooks, replay-safe
loops, boot validation, a serious test suite). It does **not** read like a rushed MVP. The risk is
concentrated, not diffuse: a money-precision cluster, a PII-leak cluster, and a few silent-failure
paths — each surgical. The architecture is sound for launch and has clearly-identified, well-ordered
scaling moves (asyncpg/PgBouncer → task queue → tracing) that do not require throwing anything away.

The single most important cultural fix is the **CI coverage gate**: the team has written the right
tests but isn't enforcing the per-path floors it documented, so quality can silently erode. Close
that and the money/PII blockers and Spinr is in better shape than most platforms at first public
launch.

### Prioritized plan (do in this order)

**P0 — blockers, this sprint (each ≤3 files, one logical change, regression test required):**
1. `confirm_payment` → 503 when Stripe key absent (C1).
2. Remove silent user-creation fallback → 503 (C2). ⊕
3. OTP DB-error re-raise before counter increment (C3).
4. Strip name/email from Stripe customer create (C4); rider name → alias in WS/FCM (C5).
5. Idempotency guard on `wallet_transactions.reference_id` (C6).
6. Receipt line-item split (base/distance/time separate) + kill `float()` in money paths
   (drivers.py:4951 no-show fee, rides.py:3646 earnings/T4A, fee line items). ⊕ (money + security)
7. Corporate payment-source priority: try rider wallet before allowance.
8. Per-file coverage floors in CI (ACTION_ITEMS A1).
9. Dispatch presence-filter: narrow catch + emit the mandated metric (E1). ⊕

**P1 — before launch:** break-glass JTI revocation (C7); payment-notify/sub-cancel logs DEBUG→ERROR
(E2); surge spatial-count + leader lock (perf #1); leaderboard N+1 (perf #2); dispatch quest/claim
batching (perf #3/#4); dispute-webhook + corporate-saga tests; `financial_events` ledger row on
refund; production CORS localhost gating; 6-digit OTP decision.

**P2 — scaling / operational:** asyncpg + PgBouncer on hot paths; extract scheduled loops off web
replicas / Redis queue; OpenTelemetry tracing + cross-replica Prometheus; feature flags + dispatch
kill-switch; Redis HA + Streams for dispatch events; staging env (unblocks load tests + RLS
integration tier); trim VM footprint and mobile vendor sprawl.
