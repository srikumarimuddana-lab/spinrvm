# Spinr — Engineering & Architecture Teardown (2026-07-16)

**Reviewer:** Engineering Director / Chief Architect (read-only review)
**Scope:** `backend/` (FastAPI, 613 modules), `shared/`, `rider-app/`, `driver-app/`,
`admin-dashboard/`. Grounded in the code at `main@a2a85c3`.
**Nature:** Read-only teardown + prioritized plan. No code was modified.

> **Bottom line up front:** This is a *mature, security-conscious* codebase, well
> past MVP. The headline sprint P0s (first-rating crash, GPS OOM, HttpOnly token
> storage, admin TTL, SOS silent-fail, fare-collection state mismatch) are all
> **verified fixed** in current code. What remains is a *second tier* of real but
> lower-severity defects — most of them concentrated in the **wallet payment path**
> and the **dispatch concurrency model** — plus architectural gaps that separate a
> strong single-region product from an Uber/Lyft-scale platform. This document is
> the plan to close them.

---

## Market context — where Spinr sits vs Uber / Lyft

| Dimension | Spinr today | Uber / Lyft | Gap assessment |
|---|---|---|---|
| **Money correctness** | `Decimal`-only, pinned Stripe API version, webhook idempotency via `stripe_events` PK, reconciliation cron | Ledger-based double-entry, dedicated payments service | Spinr's *design* is sound; execution has drift (see wallet path). Not a scale gap — a consistency gap. |
| **Dispatch** | In-process asyncio matching, atomic driver claim, batch offers | Dedicated matching service (Uber's "DISCO"), geosharded, sub-second global | Spinr is single-process + Supabase-read-heavy. Fine for Saskatchewan launch; the concurrency model (D1) and N+1 reads (D2/D3) will bite at multi-city scale. |
| **Background work** | 24 in-process asyncio loops on every replica, Redis leader-lock election | Durable job queues (Kafka + workers), exactly-once semantics | **Biggest architectural gap.** No real queue; replay-safety is hand-rolled per loop and one loop (surge) lacks a backstop. |
| **Observability** | Sentry + Prometheus metrics + audit tables + loguru bridge | Full distributed tracing (OTel/Jaeger), SLO dashboards, on-call automation | Solid logging/metrics; **no distributed tracing.** Cross-service latency is invisible. |
| **Regulatory** | PIPEDA + SK Transportation Act + SGI insurance periods baked in | Generic + per-jurisdiction bolt-ons | **Spinr's genuine moat.** Insurance-period audit trail and 0% commission are ahead of the incumbents for this market. |
| **Test depth** | 327 backend test files, E2E ride lifecycle, perf baselines | Massive but proprietary | Strong for company stage. Gaps are in the *newly-found* defect areas (wallet, dispatch races). |

**Verdict on positioning:** Spinr is not trying to be a global Uber clone and
shouldn't. Its architecture is *appropriate* for a Saskatchewan-first launch. The
recommendations below are about **hardening the current tier** and **removing the
two or three ceilings** that would block a second-province expansion — not about
prematurely adopting hyperscale infrastructure.

---

## 🚨 Critical Issues & Security Flaws

Ranked by blast radius. None are "the site is on fire," but the wallet cluster is
a revenue-integrity problem and should be treated as the top priority.

### C1 — Wallet ride-payment undercharges tax + area fees *(High)*
`backend/routes/wallet.py:249-257` bounds the debit to `total_fare`, but every other
settlement path (`payments.py:54-76` `_authoritative_ride_charge`, webhook underpay
guard `webhooks.py:594-598`) treats **`grand_total`** as the amount owed —
`total_fare` is only the fare-side subtotal and *excludes area/booking fees and
GST/PST*.
**Impact:** a rider paying by wallet settles less than a card payer on the identical
ride; collected tax and area fees are silently dropped. This is a
**revenue-integrity + tax-remittance** defect (GST/PST must be collected and
remitted — SK regulatory).
**Root cause:** the wallet path re-implemented settlement instead of reusing the
hardened helper.
**Fix:** compute the debit ceiling via the shared `_authoritative_ride_charge`
(`grand_total`, fallback `total_fare` for legacy rows); make the DB RPC's
`fare_underpaid` check use `grand_total`. **Add a regression test** asserting a
wallet ride with tax debits `grand_total`, not `total_fare`.

### C2 — Wallet top-up ships rider PII (email + legal name) to Stripe *(High — PIPEDA)*
`backend/routes/wallet.py:169-175` calls `stripe.Customer.create(email=…, name=…)`,
directly contradicting the documented control in `payments.py:120-138`
(`get_or_create_stripe_customer`): "PIPEDA (C4): do NOT send the rider's email or
legal name to Stripe (a US processor)… only `metadata.user_id` is needed."
**Impact:** Canadian rider PII crosses the data-residency boundary the rest of the
codebase enforces. This is a **compliance regression**, not just a style issue.
**Fix:** reuse `get_or_create_stripe_customer` from the wallet flow; never
re-implement `Customer.create` with PII.

### C3 — Surge cap (2.5×) is enforced on *read*, never on *write* *(Medium→High, regulatory)*
The provincial 2.5× ceiling is clamped independently at three fare read sites
(`features.py:995`, `routes/fares.py:235`, `services/fare_service.py:399`) via
`min(stored, SURGE_CAP)`. The admin write path only **warns** when storing
`surge_multiplier > SURGE_CAP` (`routes/admin/service_areas.py:533-544`) — it does
not reject — and the **driver offer payload uses the raw stored value**
(`matching.py:754, 826`).
**Impact:** the invariant depends on *every current and future read path* remembering
to clamp. A single unclamped read ships a >2.5× regulated price — a provincial
compliance breach and a reputational event.
**Fix:** enforce at the source of truth — reject (or store the clamped value) on
write in the admin service-area endpoint. Keep the read clamps as defense-in-depth.

### C4 — Concurrent dispatch attempts over-offer beyond `max_offers` *(High, correctness/UX)*
`_match_driver_to_ride_attempt` (`routes/rides/matching.py:151`) guards only on
`ride.status == SEARCHING`. The ride stays `searching` for the whole offer window,
and **four entry points can fire concurrently** for the same ride (batch timeout
handler, `decline_ride` re-dispatch, durable reaper, no-drivers retry). The atomic
*driver* claim prevents double-claiming the same driver, but two overlapping attempts
claim *different* drivers and each insert up to `max_offers` offer rows → **up to
2×`max_offers` simultaneous offers** and duplicate FCM pushes.
**Impact:** drivers get phantom offers for already-dispatched rides; the >2s KPI is
threatened; wasted driver attention erodes supply-side trust.
**Fix:** add an atomic **ride-scoped dispatch lease** (conditional
`dispatch_in_progress` flag or Redis `SET NX dispatch:{ride_id}`) a second attempt
must acquire before claiming/offering.

### C5 — OTP hash is unsalted/un-peppered SHA-256 over a 4-digit secret *(Medium)*
`utils/crypto.py:10-29` — `sha256(code)` with a 10,000-value keyspace and no
salt/pepper is trivially reversible from a single DB read (`otp_records.code`).
The 5-fail/24h lockout protects the *online* path, not *offline* reversal of a leaked
hash.
**Impact:** a service-role-key leak, RLS gap, or backup exposure converts to account
takeover inside the 5-minute window.
**Fix:** HMAC-SHA256 with a server-side pepper (e.g. derived from `JWT_SECRET`):
`hmac.new(pepper, code, sha256)`.

### C6 — Production CORS hard-allows `localhost` with credentials *(Medium)*
`core/middleware.py:570-583` unconditionally extends allowed origins with
`http://localhost:3000/3001` regardless of `ENV`, and `allow_credentials=True`. The
prod fail-fast only rejects the `*` wildcard, not localhost.
**Impact:** any locally-served page (malware, a phished localhost app) can make
credentialed cross-origin calls against production.
**Fix:** gate the localhost entries behind `if not is_production`.

### C7 — Admin-assigned rides have no durable offer-expiry recovery *(Medium→High, safety of state)*
`admin_create_ride` (`routes/admin/rides.py:952`) assigns a driver and arms the
**legacy** `_offer_timeout_handler` (`matching.py:895`) *without inserting a
`ride_offers` row*. That handler is non-atomic and, unlike `process_expired_offer`,
has no durable backstop: `stuck_ride_sweeper` only cancels `searching` rides and
`offer_expiry_reaper` only acts on `ride_offers` rows.
**Impact:** an admin-assigned ride whose in-process timer is lost on a pod restart
can sit in `driver_assigned` **forever** — a stuck ride with a real rider waiting.
**Fix:** route admin-assigned dispatch through the same `ride_offers` +
`process_expired_offer` machinery and retire `_offer_timeout_handler`.

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

**Overall:** among the strongest areas of the codebase. `verify_otp` deliberately
avoids interpolating exceptions into client detail (`auth.py:1055-1071`); Stripe
handlers return sanitized 402/502/500 while logging `exc_info` server-side; phone
numbers are masked to last-4 or hashed in logs; Sentry has `send_default_pii=False`
and a loguru→Sentry bridge. The "don't swallow DB/auth/payment errors" rule is
largely honored. Remaining gaps:

### T1 — ErrorBoundary renders raw stack traces to production users *(Medium)*
`shared/components/ErrorBoundary.tsx:40,47-55` intentionally shows
`error.name: error.message` **plus the full component/JS stack** in *release* builds
(root boundaries in both apps). `captureException` already ships the same data to the
error service, so the on-screen dump is redundant.
**Impact:** leaks internal module/file names to end users (recon aid) and is poor
consumer UX — the exact "no raw errors leak to the user" rule, violated on the
client.
**Fix:** gate the diagnostics block behind `__DEV__` / a remote debug flag; in
production show only "Something went wrong" + Try Again. Keep full detail flowing to
Sentry.

### T2 — DB helpers convert "database down" into "no rows" *(Low→Medium)*
`repositories/_base.py:559-628` — when the Supabase client is unset, `get_rows→[]`,
`count_documents→0`, `insert_one/update_one→None`. `update_one` returning `None` is
the *same* signal callers use for "0 rows / not found."
**Impact:** an outage masquerades as normal empty data — the swallow-and-continue
class this codebase explicitly forbids. Bounded today because prod fail-fasts on an
unset Supabase URL, but any degraded/lazy init path would hide an outage.
**Fix:** raise `ServiceUnavailableException`/`DatabaseError` instead of returning
empty sentinels when `supabase is None`.

### T3 — Log-correlation decodes JWT without signature verification *(Low, log integrity)*
`core/middleware.py:213-227` — `jwt.decode(..., verify_signature=False)` to tag logs
with `user_id`. A forged token poisons log correlation (misattribution). No access is
granted (real authz re-reads from DB), but audit integrity is skewable.
**Fix:** tag logs with the `user_id` resolved by the authenticated dependency, or
clearly mark the field as untrusted.

### T4 — Loop watchdog omits ~7 spawned loops → silent staleness *(Medium)*
`core/lifespan.py:415-435` `_WATCHDOG_LOOP_NAMES` lists 17 of ~24 spawned loops.
Missing: `preauth_capture`, `referral_payout`, `driver_claim_reaper`,
`safety_checkin`, `reconciliation`, `suspension_reactivation`, `zoho_desk_sync`.
**Impact:** if `driver_claim_reaper` wedges, driver supply silently erodes; if
`safety_checkin` wedges, a **trust-and-safety** loop dies with no alert.
**Fix:** derive the watchdog list from the actual `background_tasks` registry so new
loops are covered by construction.

**Telemetry gap (strategic):** there is **no distributed tracing** (no OpenTelemetry).
Metrics + logs + Sentry answer "what broke," but not "where did the 1.8s of dispatch
latency go across DB → Redis → Google Distance Matrix → FCM." See D-rec below.

---

## 🐢 Performance Bottlenecks & Optimizations

### P1 — Service-area row re-read 5–6× per dispatch attempt *(Medium)*
One `match_driver_to_ride` attempt fetches the same `service_areas` row repeatedly:
`resolve_matching_config` (`dispatch_service.py:270`), subscription filter
(`matching.py:343,348`), quota `area_timezone` (`:439`), cascade (`:473,478`),
polygon fetch (`:736`). Multiplied by every 10s retry on every replica.
**Fix:** fetch area (+ parent) once at the top of the attempt and thread it through.
Low-risk, immediately cuts Supabase egress on the hot path.

### P2 — Per-driver N+1 in the notify loop *(Medium)*
`matching.py:648` (`get_driver_by_id` per claim) and `:763-770` (`quest_progress`
query per driver) run on the <2s dispatch path. Bounded by `max_offers` (≤10) but
serialized.
**Fix:** batch the quest lookup with one `IN` query keyed by claimed `user_id`s.

### P3 — Admin driver-location fan-out is O(drivers × admins) every 3s *(Medium)*
`socket_manager.py:377` throttles per-driver to 1 msg/3s but still sends **every**
driver's location to **every** admin socket fleet-wide. The code itself flags
viewport filtering as unbuilt (`:383-386`).
**Impact:** at fleet scale this is the dominant WS load.
**Fix:** admin viewport (bbox) subscription so only in-view drivers are sent.

### P4 — Surge status endpoint recomputes demand+supply live per request *(Medium)*
`surge_engine.py:317-327` calls `_count_demand/_supply_in_area` for *every* area on
*each* dashboard load, each a fetch of up to `_SURGE_FETCH_CAP=5000` rows.
**Fix:** serve from the last `surge_pricing` snapshot instead of recomputing live.
Also note **SU3**: supply/demand counts truncate at 5000 rows and only log — a
truncated supply undercount *over-prices* a regulated fare. Ship the flag-gated
PostGIS spatial count (`SURGE_SPATIAL_COUNT`) as the real fix.

### P5 — `surge_pricing` history row inserted every tick regardless of change *(Low→Medium)*
`surge_engine.py:289-303` writes the DB multiplier only on change but inserts a
history row unconditionally every 2 min per area → steady unbounded growth (mitigated
only by retention purge).
**Fix:** insert only on multiplier change, or downsample.

### P6 — Inline Stripe pre-auth on the booking hot path *(Medium, by design)*
`routes/rides/booking.py:861` awaits `_preauthorize_ride_card` (a Stripe round-trip)
inline before insert. Deliberate (catch a dead card before disturbing a driver), but
a slow Stripe adds directly to booking latency.
**Fix:** confirm a tight client-visible timeout wraps the pre-auth so a Stripe stall
can't hang the booking request; consider a fast-path for previously-validated cards.

---

## 💡 Tech Stack & Architecture Recommendations

The stack is modern and well-chosen (FastAPI 0.136, Python 3.12.9 SHA-pinned,
Pydantic 2, Supabase/Postgres+RLS, Redis 7.4, Stripe 15, Sentry 2.59, SlowAPI). Four
gaps matter for the *next* stage:

### A1 — Introduce a durable task queue *(highest-leverage architectural change)*
**Problem:** 24 background loops run in-process on every replica; replay-safety is
hand-rolled per loop and **surge lacks a DB-claim backstop** (`surge_engine.py:373-379`
falls through to `is_leader=True` on Redis failure, and the in-memory Redis fallback
returns `True` on *every* replica — so with `REDIS_URL` unset on >1 replica, every
replica writes a regulated price and inserts duplicate rows). This is fragile and
caps horizontal scaling.
**Why it's a problem:** duplicate charges/notifications/prices under partition or
misconfig; no retry/backoff/dead-letter semantics; no visibility into queue depth.
**Recommendation:** adopt **`arq`** (Redis-native, asyncio, minimal — fits the
existing Redis dependency) or **Dramatiq** for the money/notification loops. Keep
lightweight sweepers as loops, but move charge/payout/notification/surge-write work
to queued jobs with idempotency keys and dead-letter handling. Interim, cheaper fix:
give **surge the same atomic DB-claim backstop** `payment_retry` already has
(`payment_retry.py:305-325`) so it can't double-write behind a failed lock.

### A2 — Add distributed tracing (OpenTelemetry)
**Why:** dispatch latency crosses DB → Redis → Google Distance Matrix → FCM; today
that path is only observable as aggregate metrics. OTel auto-instrumentation for
FastAPI + httpx + redis gives per-span latency and turns "dispatch is slow sometimes"
into "the P95 is the Distance Matrix call." Export to the existing Sentry (native OTel
support) or Grafana Tempo. Low code cost, high diagnostic payoff.

### A3 — Formalize the wallet/ledger as double-entry
**Why:** C1's undercharge is a symptom — money movement is spread across wallet RPCs,
Stripe paths, and corporate deltas with no single source of truth. A **double-entry
ledger table** (every cent is a balanced debit+credit with a reference) makes
undercharges *structurally impossible to hide* and makes the reconciliation cron a
verification, not a discovery, tool. This is how Uber/Lyft/Stripe model money
internally. Medium effort, high correctness payoff.

### A4 — Read-replica / caching for immutable-ish reads
`service_areas`, subscription tiers, and polygons are read constantly on the dispatch
hot path (P1). A short-TTL in-process/Redis cache (or Supabase read replica) removes
that egress. Pairs naturally with A1's queue for cache invalidation events.

---

## 🛠️ Maintainability & Code Smells

- **M1 — Delete the deprecated `frontend/` app.** It still carries the *pre-hardening*
  insecure token pattern (`frontend/store/authStore.ts:22-32,184` writes the raw JWT
  to `sessionStorage`). It is CI-disabled (`if: false`) and not built, but it's a
  live foot-gun: accidental re-enable, or a dev copying the pattern. Remove or archive
  out of the repo. *(Low, but high signal-to-noise cleanup.)*
- **M2 — Remove dead web token-storage branches in the shared client.**
  `shared/api/client.ts:248-261` reads `sessionStorage.auth_token` that
  `authStore.setTokens` never writes on web (`authStore.ts:96-99`), and the 401
  handler clears **`localStorage`** while the read uses **`sessionStorage`** (`:904`
  vs `:250`). Dead + mismatched → invites a future XSS-reintroducing "fix."
- **M3 — Wallet path duplicates payments logic.** C1/C2/C3 all stem from
  `wallet.py` re-implementing customer creation, idempotency keys
  (`wallet.py:188-190` omits amount → 1-min-bucket collision), API-version pinning
  (`:196-200` uses `stripe.api_version` not the pinned `STRIPE_API_VERSION`), and
  settlement instead of importing the hardened `payments.py` helpers. **Consolidate
  onto the shared helpers** — this single refactor closes four findings.
- **M4 — Money-as-float latent spots.** `fare_service.py:31-37` `DEFAULT_FARE` stores
  money as float literals (currently exact values). Convert to `Decimal`/string
  constants to keep the "no float in money" rule airtight.
- **M5 — Surge cap enforcement scattered across 3 read sites** (C3) is itself a
  maintainability smell: an invariant enforced in N places will eventually be missed
  in the N+1th. Enforce once, on write.

---

## 🧪 Testing & QA (Missing Edge Cases)

327 backend test files, E2E ride lifecycle, and perf baselines are a strong
foundation. The gaps track exactly the newly-found defects — write these regression
tests *before* the fixes:

1. **Wallet settlement uses `grand_total`** (C1): a wallet ride *with tax + area fee*
   must debit `grand_total`, not `total_fare`. Currently untested — this is why the
   bug survived.
2. **Wallet top-up idempotency** (M3): two different amounts by the same user in the
   same minute must not collide (assert distinct idempotency keys).
3. **Wallet top-up PII** (C2): assert `stripe.Customer.create` is called with **no**
   `email`/`name` (metadata only).
4. **Surge cap on write** (C3): admin setting `surge_multiplier = 3.0` must be
   rejected or stored clamped to 2.5; a driver offer payload must never carry >2.5×.
5. **Concurrent dispatch** (C4): two overlapping `match_driver_to_ride(ride_id)`
   calls must not produce >`max_offers` total offers.
6. **Admin-assigned ride recovery** (C7): an admin-assigned ride must be recoverable
   by the durable reaper after a simulated timer loss.
7. **OTP hash** (C5): assert stored hash is not a bare `sha256(code)` (peppered/HMAC).
8. **ErrorBoundary in release mode** (T1): production render shows friendly copy, no
   stack.

**QA process note:** the wallet path clearly wasn't exercised through the same
adversarial fare/tax test matrix as the Stripe path. Extend the fare test matrix to
run *every* settlement method (card, wallet, corporate, promo) through the same
tax/area-fee assertions — a single parametrized suite would have caught C1.

---

## 📈 Manager's Verdict — Overall Code Health

**Grade: B+ / strong.** This is a disciplined, security-aware, regulation-first
codebase that has clearly survived several serious audit sweeps. The conventions
(Decimal money, atomic state guards, RLS-first migrations, append-only audit tables,
PII-scrubbed logs, replay-safe-by-design loops) are genuinely good and mostly
followed. The team's instinct to *fail loud* on DB/auth/payment errors is exactly
right and rare.

**What holds the grade back from A:**
1. **The wallet payment path is a consistency island** — it re-implements what
   `payments.py` already hardened, and in doing so regresses tax collection (C1), PII
   residency (C2), idempotency (M3), and API-version pinning. One consolidation
   refactor + one parametrized fare-test suite closes the whole cluster. **This is the
   #1 priority** — it's revenue/tax/compliance, not cosmetics.
2. **The dispatch concurrency model is single-guard** (C4) and the admin-assigned
   path has no durable recovery (C7). Fine at launch volume; a real risk at scale.
3. **One architectural ceiling** — background work has no durable queue and surge has
   no backstop (A1). This is the thing to fix *before* a second-province expansion,
   not after.

**What's genuinely ahead of the incumbents for this market:** the insurance-period
audit trail, 0%-commission model, PIPEDA/SK-Transportation-Act enforcement in code,
and the honesty of the error-handling posture. Don't trade these away chasing
Uber-parity features.

### Recommended sequencing (the plan)

| Phase | Items | Rationale |
|---|---|---|
| **P0 — this sprint** | C1, C2, C3 (wallet cluster + surge-on-write), C7 | Revenue, tax, PIPEDA, regulated pricing, stuck-ride safety. All small, high-value. |
| **P1 — next sprint** | C4 (dispatch lease), C5 (OTP HMAC), C6 (CORS), T4 (watchdog), M1/M2 (dead-code delete) | Correctness + security hardening; low-risk cleanups. |
| **P2 — hardening** | P1–P6 perf, A2 (OTel tracing), A4 (read cache) | Scale readiness + diagnosability. |
| **P3 — architectural** | A1 (durable queue), A3 (double-entry ledger) | Removes the pre-expansion ceilings. Plan deliberately, don't rush. |

Every fix above should ship with the matching regression test from the Testing
section, one logical change per commit, per the repo's batch-size rule.

---
*Read-only teardown — no source files were modified in producing this document.*
