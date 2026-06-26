# Spinr — Engineering Director's Code & Architecture Teardown

_Read-only review · 2026-06-26 · branch `claude/epic-planck-3m83ui` · HEAD `3f125bf`_
_Scope: backend (~154k LOC Python/FastAPI), realtime, dispatch, payments, plus stack vs. Uber/Lyft._
_Every finding is grounded in actual code (`file:line`). Nothing in this document was changed._

---

## Executive framing

This is **well above the median pre-launch ride-share codebase.** The realtime layer, dispatch
race-safety, replay-safe background loops, refresh-token rotation with reuse-cascade, JWT audience
binding, and PII-masking discipline are all implemented the way a mature team would. The sprint
board says "all P0s shipped" — and the *security posture* largely backs that up.

But the board's "sprint COMPLETE / all P0s shipped" status is **slightly optimistic**: this review
surfaced **two P1 correctness bugs that strand money/auth flows**, a **dead PostGIS index** that
leaves dispatch doing full-table scans in Python, and an **observability stack that is a dependency
but never initialized.** Those are the headline items. Details below, in the requested structure.

---

## 🚨 Critical Issues & Security Flaws

The auth/payments core is hardened (see "What's already right"). The real defects are correctness
bugs that wedge user-facing flows, plus one privacy-minimization gap.

| # | Severity | Finding | Location | Why it matters |
|---|---|---|---|---|
| C1 | **P1** | **Ride permanently wedged in `payment_status="processing"`.** `claim_ride_payment_processing()` atomically flips `pending → processing` *before* the validation block — which can then raise 403 / 402 `AMOUNT_MISMATCH` without ever releasing the claim. The idempotency guard treats `processing` as terminal and returns `already_processed`, so the ride can never be paid again. | `backend/routes/payments.py:356` (claim), `:365/:388/:405/:424` (raise-without-rollback), `:348-354` (terminal guard); `repositories/ride_repo.py:84-105` | A transient race or benign metadata mismatch strands a *completed* ride unpaid forever. Driver doesn't get paid; rider stuck. Violates the ride-state-machine invariant that state strings stay consistent. |
| C2 | **P1** | **OTP DB-read failure reported to the user as "wrong code" — and counts toward lockout.** If `get_otp_record_by_phone` raises, the exception is logged but `otp_record` stays `None`; flow falls through to `_record_otp_failure()` → `ERR_OTP_INVALID`. The sibling `send-otp` / find-user paths in the same file correctly raise 503 — this read path was missed. | `backend/routes/auth.py:384-395` (vs. correct 503 at `:300-310`, `:499-504`) | During a Supabase blip, legitimate users are told their *correct* code is invalid **and** pushed toward the 5-fail/24h lockout. Exactly the "swallow DB/auth error and continue" pattern CLAUDE.md forbids. |
| C3 | **P2** | **Exact pickup/dropoff addresses persisted to financial rows and forwarded to Stripe (US).** Written (200-char-truncated, still exact) into `financial_events.metadata`, `wallet_transactions.metadata`, **and Stripe PaymentIntent metadata** — sending exact-address PII to US third-party infra. | `backend/services/payment_service.py:110-111`, `:219-220` | Data-minimization + residency gray area (CLAUDE.md bans exact addresses leaving the store; PIPEDA residency rule). Not a log leak — a stored-data privacy issue. Store `ride_id` and resolve on demand, or city/area only. |
| C4 | **P2/P3** | **Notification-failure swallowing on refund/cancel paths** logged at `logger.debug` (below warning) — a broken push pipeline for refund/cancel notices is invisible. Inconsistent with `payout.failed` (warning). | `backend/routes/webhooks.py:743-744`, `:957-958`; borderline `:280-281` | Underlying DB write already succeeded, so impact is a silently-missed user notification, not lost money — but it's exactly the inconsistency that hides a degraded push pipeline. |
| C5 | **P3** | Card `brand` + `last4` logged. Stripe treats last4 as non-sensitive, but CLAUDE.md's wording is categorical ("never log even masked PANs"). | `backend/routes/payments.py:732` | Literal convention violation, low real risk. |

**What's already right (verified, notable given the scope):** Stripe idempotency (single
`claim_stripe_event` gate at `webhooks.py:424` before dispatch, signature verified before field
access); JWT audience binding on both Firebase and legacy paths with non-admin role always re-read
from DB; refresh rotation with reuse-detection cascade; no `detail=str(e)`/stack-trace leakage to
clients anywhere in the audited routes; phone masked to last-4, no raw lat/lng in logs
(`analytics.py:351-371` actively rejects coords); fail-fast prod config guards on weak
secrets/non-`ca-` region (`core/config.py:198-267`).

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**User-facing:** Strong. No raw exceptions or stack traces reach clients; DB errors route through
`db_error_text()`/`pg_error_code()` → clean 503/502. The two exceptions are **C2** (DB error shown
as "wrong OTP") and the wedged-payment **C1** (terminal `already_processed` masking a real failure).

**Admin-facing telemetry — the biggest structural gap:**

- **Sentry is a dependency and a config field but is *never initialized*.** `sentry-sdk` is in
  `requirements.txt`, `sentry_dsn` exists in `core/config.py:179`, but there is **no
  `sentry_sdk.init(...)` anywhere in `core/`.** The "OTel trace ID" in `core/middleware.py:160-195`
  is a homegrown request-id, not real tracing. **You have the domain/surface/ride_id tagging
  *convention* documented in CLAUDE.md, but nothing emits it.**
- **Consequence:** CLAUDE.md defines 8 P95 SLAs, but with Prometheus metrics only you see *that*
  dispatch P95 breached 2s, never *which* span (candidate fetch / presence filter / claim / WS
  publish) ate the budget. Every future latency regression becomes a guessing game.
- **Fix (highest leverage-per-hour on this whole report):** wire the missing `sentry_sdk.init` with
  `traces_sample_rate`, add `opentelemetry-instrumentation-fastapi` + httpx auto-instrumentation so
  every PostgREST/Stripe/Twilio call is a span. A few lines of init unlock root-cause on every SLA
  breach and make the asyncpg migration (below) measurable.

---

## 🐢 Performance Bottlenecks & Optimizations

Ordered by severity (SLA threatened × frequency). All verified in code.

### P0 — Dispatch hot path (threatens *offer → notify < 2s*)
- **The PostGIS geospatial index is dead code; dispatch does a full-table scan + Python haversine.**
  Dispatch reads up to 500 driver rows with a non-spatial filter and computes haversine per
  candidate in-process (`routes/rides.py:694-720`, `services/dispatch_service.py:160`). The
  `find_nearby_drivers` PostGIS RPC **exists but is bypassed** because `update_driver_location`
  never populates the PostGIS `location` column (`routes/rides.py:696-698`,
  `repositories/driver_repo.py:107`). **Fix: populate the `geography(Point)` column on location
  write, add a GIST index, route dispatch through the RPC that's already written.** O(all-online
  drivers) → indexed lookup. Cheap now, expensive to retrofit post-launch.
- **N+1 quest-progress query per claimed driver inside the notify loop** —
  `routes/rides.py:1174-1201` runs a separate `quest_progress` SELECT per offer (up to 10),
  sequential awaits, *before* the offer is even sent. The rider/incentive enrichment right above it
  (`:1156-1160`) already does this correctly with `asyncio.gather`. **Fix: one batched `$in` fetch
  → in-memory map.**
- **`claim_driver_atomic` + `get_driver_by_id` re-read, sequentially, per candidate**
  (`routes/rides.py:1057-1071`) — 2×N sequential round-trips on the <2s path. **Fix: extend the
  atomic claim to `RETURNING` the full eligibility set, drop the re-read.**

### P1 — Fare estimate (threatens *< 300ms*)
- **No caching on fare-estimate reference reads.** `fares_for_location()` does 2–3 uncached DB
  round-trips per estimate on near-static tables — `vehicle_types`, `service_areas`, `fare_configs`
  (`services/fare_service.py:354-399`). **Fix: 30–60s Redis/in-process cache, exactly like
  `settings_loader.get_app_settings` already does (`settings_loader.py:17-54`); invalidate on admin
  write.**

### P2 — Background loops (every replica)
- **Surge engine: full `drivers` scan (up to 5000 rows) + Python point-in-polygon per area, every
  2 min, on every replica** (`utils/surge_engine.py:160-195`, `:247`). The PostGIS path
  (`_count_supply_spatial`, `SURGE_SPATIAL_COUNT` flag, migration 170) **exists but is off by
  default** — enabling it replaces the scan with index-only `ST_Covers`. Also `get_surge_status`
  recomputes per area on every admin poll (`:316-325`) — cache it.
- **`recalculate_all_surges` writes 1 history row per area per tick, sequentially, with no leader
  lock** (`:288-302`) — N replicas write N× the rows. **Fix: `insert_many` + Redis `SET NX` leader
  lock** (the codebase already uses this for `retention_purge`/`reconciliation`).
- `core/lifespan.py:96` uses deprecated `asyncio.get_event_loop()`.

### P2 — Admin N+1 / unpaginated scans
- Referral leaderboard nested N+1 (`routes/admin/drivers.py:1690-1707`, `:1607-1613`, `:1766-1771`)
  — O(referrers × referees) round-trips. Payout batch-retry by id loops `find_one` per id
  (`routes/admin/rides.py:3160-3161`). **Unpaginated aggregation reads** — `admin/messaging.py:57`
  reads **50,000** ride rows to extract distinct rider_ids; `admin/support.py:157` reads the whole
  disputes table; `subscriptions.py:219` uses limit=20000. **Fix: push `DISTINCT`/`GROUP BY` into
  SQL RPCs; batch with `.in_()`.**

### P2 — WS fan-out (otherwise healthy)
- **3 sequential Redis round-trips per WS message** — `publish()` does INCR, then a 3-op
  rpush/ltrim/expire pipeline, *then* the actual publish (`utils/ws_pubsub.py:174-197`), and
  `broadcast_ride_status` fires it 2–3× per transition. Measured against the `<100ms` SLA. **Fix:
  fold the outbox sequence into one Lua script, or fire-and-forget via `create_task` off the publish
  critical path.** (Fan-out is otherwise correctly targeted — no broadcast-to-all on the hot path.)

**Ruled out (verified non-issues):** FCM/Twilio are *not* awaited inline on hot paths (dispatch push,
safety ticket, ETA refresh all offloaded via `create_task`); presence is batched (single MGET); ETAs
batch to one Distance Matrix call per 25 origins; the DB circuit-breaker + retry budget are well-built.

---

## 💡 Tech Stack & Architecture Recommendations

**What's good — keep it, don't over-engineer:** Socket.IO-Redis-adapter-style WS fan-out with
cross-replica unicast, sequenced reconnect outbox, and degraded local-only fallback
(`utils/ws_pubsub.py`) — the one subsystem that already looks like Uber/Lyft. Atomic dispatch claims.
18 replay-safe loops with Redis leader locks. A two-sided load-test harness already exists
(`loadtest/locustfile.py`). Spatial/observability debt is *tracked and gated*, not hidden.

| Priority | Gap | Recommended tech | Why / root cause |
|---|---|---|---|
| **P0** | Geospatial matching is an app-tier full scan | **PostGIS GIST on `geography(Point)`** (already migrated); **H3** (`h3-py`) later for surge supply/demand buckets | The index exists and dispatch deliberately bypasses it. Fixes dispatch scan, surge mis-pricing, *and* the deferred corporate geofence at once. Lowest cost now, highest retrofit cost later. |
| **P1** | `supabase-py` REST (PostgREST) per call, capped at a 64-thread pool | **asyncpg + connection pool** for the ~5 hot paths (dispatch fetch, fare estimate, location write); keep supabase-py for long-tail CRUD | Every DB op is a sync HTTP round-trip through a 64-thread ceiling (`repositories/_base.py:136-200`). Under a Saturday spike, thread-pool saturation bottlenecks before Postgres does. Supabase ships a direct PG string + PgBouncer for exactly this. |
| **P1** | No distributed tracing despite 8 SLAs | **OpenTelemetry → existing Sentry** (init is missing) | Covered above. Few lines, unlocks root-cause forever. |
| **P2** | No message queue; offload rides on polling loops | **Redis Streams** (`XADD` + consumer groups; zero new infra) for OTP-send, push fan-out, Stripe side-effects. *Not* Kafka — gold-plating for SK volume | Polling every 5 min is a poor substitute for event-driven at-least-once retry. The loops are *correct*, just not lowest-latency. |
| **P2** | Dispatch is a "service" by file, not deployment | **Don't split now.** Add an env flag to pin latency-sensitive loops to a dedicated worker so a future replica split is config-only | Monolith is right for one province. The failure mode is noisy-neighbor latency (admin analytics vs. dispatch on the same 64-thread pool), not correctness. |
| **P3** | No feature flags; no payments event-ledger | **Unleash** (self-host, free) or a typed flags table; append-only `*_events` ledger feeding reconciliation | Kill-switch/dark-launch for surge & dispatch algo. For a 0%-commission model, payout correctness *is* the product — an immutable ledger beats daily polling reconciliation. |

**Scalability ceiling:** "single FastAPI process + supabase-py REST + 18 in-process loops per
replica" holds fine for a Saskatchewan launch. It breaks on: (1) the 64-thread DB pool under spike,
(2) the per-replica surge scan multiplied across the fleet, (3) noisy-neighbor latency between admin
queries and the dispatch hot path. All three are addressed by the P0/P1 items above without a rewrite.

---

## 🛠️ Maintainability & Code Smells

- **God files.** `routes/drivers.py` is **7,833 lines** (`complete_ride()` alone is **766 lines** at
  `:4049`; `update_driver_status()` is 507 at `:5557`). `routes/rides.py` is **6,038 lines**
  (`create_ride()` is **899 lines** at `:2357` — the single worst function in the backend;
  `match_driver_to_ride()` 664 at `:636`). These are unreviewable units — split by sub-domain.
- **`_d()` / `_round()` money helpers copy-pasted into 17 files** (`wallet.py:50`, `quests.py:30`,
  `corporate_company.py:437`, `fare_service.py`, `payment_service.py`, …) instead of one
  `utils/money.py`. Divergence risk on the *most safety-critical* helper in the codebase.
- **Float leaks in admin money paths.** The Decimal-only pre-commit guard scopes only fare code;
  `routes/admin/subscriptions.py:432-436` (`total/subtotal/gst/pst`), `admin/maintenance.py:219-222`
  (earnings/tips), `admin/incentives.py:92` (bonus) cast dollars to `float`. 217 `float(...)`
  call-sites in routes/services overall.
- **Dual `try/except ImportError` pattern repeated 166×** — intentional, but pure boilerplate that
  could be centralized in one import shim.
- **God nodes (graphify):** `request()` (257 edges), `SpinrException` (217), `run_sync()` (120),
  `get_app_settings()` (120) — `run_sync` and `get_app_settings` are single points every DB/settings
  path funnels through; 120-edge blast radius on any change. 6 self-referential 1-file import cycles
  to confirm aren't real circular imports.
- **Positive:** TODO/FIXME density is genuinely low — only 12 hits, all false positives
  (`DRV-XXXXXX`-style format docstrings). The debt is in file size and duplication, not abandoned markers.

---

## 🧪 Testing & QA (missing edge cases)

- **The money-path coverage mandate is aspirational, not enforced.** Global floor is
  `--cov-fail-under=60` (`backend/pytest.ini:15`, baseline 53.8%). CLAUDE.md mandates **≥90%**
  (payments, fare_service) and **≥80%** (rides, dispatch) — **no per-module gate exists** in CI. A
  payments regression can land green at ~55%. This is `ACTION_ITEMS.md` A1, still open, and the audit
  confirms it's the single biggest gap. **Fix: `coverage report --include=… --fail-under=90` CI step.**
- **Coverage-gaming smell:** `test_coverage_boost.py` / `test_coverage_payments.py` /
  `test_coverage_rides.py` — audit they assert real branches, not import-and-call.
- **The surge HARD CAP (2.5×) has no explicit assertion.** Tier boundaries are well-covered
  (`test_surge_engine.py`, 29 tests), but nothing asserts `ratio >= 3.0` clamps to 2.5, nor that an
  admin override > 2.5 requires justification. `test_e16_surge_boundary.py` is misleadingly named —
  it's about estimate-*token* tampering. **The "never raise SURGE_CAP" invariant is untested.**
- **Other gaps:** GST/PST separate-line-item receipt under the *combined* corporate + promo + surge
  branch; explicit negative test that `in_progress → cancelled` raises; `is_available ⇒ is_online`
  under offer-pending.
- **Strong, verified coverage (not a gap):** concurrent-accept race
  (`test_ride_accept_flow.py:250`, `test_e2e_ride_lifecycle.py:179`), offer timeout, insurance
  periods, refund rounding, WS auth/reconnect, OTP lockout, corporate fallback, full
  searching→completed E2E. 275 test files — breadth is real.

---

## 📈 Manager's Verdict

**Overall health: B+ / strong pre-launch, with a short must-fix list.** This is a disciplined,
security-conscious codebase whose hardest subsystems (realtime, dispatch race-safety, auth/refresh,
replay-safe loops) are done right, and whose technical debt is *honestly tracked* rather than hidden.
That is rare and valuable. The CLAUDE.md conventions are unusually mature and are mostly actually
followed.

The gap between the board's "sprint COMPLETE / all P0s shipped" and reality is **two P1 correctness
bugs** (C1 wedged payment, C2 OTP-error-as-wrong-code) that strand real user flows, plus **three
high-leverage, low-cost infrastructure wins that are already 90% built and just need turning on:**
PostGIS dispatch (index exists, bypassed), Sentry/OTel (dependency exists, never initialized), and
surge spatial count (flag exists, off). None require a rewrite.

**Recommended sequencing (the plan):**

1. **This week (correctness):** Fix C1 (release the payment claim on every validation failure +
   regression test) and C2 (raise 503 on OTP DB-read failure). Both are scoped, both have clear tests.
2. **Next (turn on what's built):** Wire `sentry_sdk.init` + OTel FastAPI/httpx instrumentation;
   populate the PostGIS column and route dispatch through `find_nearby_drivers`; enable
   `SURGE_SPATIAL_COUNT`. High leverage, low diff.
3. **Pre-launch hardening:** Per-module coverage gate (A1) + the surge-cap assertion; cache the
   fare-estimate reference reads; batch the dispatch quest N+1 and the admin N+1s; consolidate `_d()`
   into `utils/money.py`.
4. **Post-launch, scheduled (not blocking):** asyncpg pool on hot paths; Redis Streams offload;
   feature flags; split the two god files; the env-flag worker split for a future dispatch replica.

**vs. Uber/Lyft:** For Saskatchewan-first, 0%-commission, pre-launch scale, the architecture choices
are appropriate — the divergences from how Uber/Lyft operate (isolated matching service, H3/PostGIS
geospatial, event-sourced payments ledger, full distributed tracing) are correctly *deferrable*,
**except** server-side geospatial matching, which is the one place a market leader would never accept
an app-tier full scan and which is cheap to fix now because the index already exists.
