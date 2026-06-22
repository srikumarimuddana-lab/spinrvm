# Spinr — Engineering Teardown & Action Plan

**Date:** 2026-06-22  **Scope:** read-only review (backend + 4 frontend surfaces)  **Branch:** `claude/epic-planck-6lfkz5`
**Reviewer posture:** Staff eng / QA lead / code review / EM. No code was modified.

> Method: five parallel domain audits (dispatch/rides, payments/money, auth/security, safety/GPS, frontend),
> with the most severe claims re-verified directly against source. Findings cite `file:line`.

---

## TL;DR — Sprint reality check

The active sprint lists six P0s. The review found **two are already fixed**, and the others are real but
narrower than the ticket text:

| Sprint P0 | Status after review |
|---|---|
| HttpOnly token storage | ✅ **Already remediated.** Access token is memory-only; refresh token in `expo-secure-store` / HttpOnly cookie. No AsyncStorage/localStorage token persistence on any surface. |
| First-rating crash | ✅ **Not reproducible.** Every rating submit/display path is guarded (`|| 5.0`, `Number.isFinite`, `rating > 0`). Looks already hardened — ask ticket author for the stack trace. |
| Admin TTL reduction | ✅ Already `ADMIN_ACCESS_TOKEN_TTL_HOURS = 1` (`config.py:55`); sprint docs still say 12h — update them. |
| SOS silent failure | ⚠️ **Live.** Unguarded incident insert kills the whole fan-out (`rides.py:4678`). |
| Fare-collection state mismatch | ⚠️ **Live tail.** Completion and settlement are two un-linked writes; no reconciliation loop for `completed + payment_status=pending`. |
| GPS OOM | ✅ Mostly mitigated (caps exist); residual is a per-driver buffer leak if WS-disconnect cleanup is skipped. |

Net: the codebase is **more mature and security-conscious than the backlog implies**. Real remaining risk is
concentrated in four places: SOS, a driver-supply leak in dispatch, fare settlement reconciliation, and a
handful of auth-hardening items.

---

## 🚨 Critical Issues & Security Flaws

**Auth / JWT trust model** (`backend/dependencies/__init__.py`) — verified against source:
- **`:406-428` — JWT path auto-creates a user row when none exists.** Any validly-*signed* token for a
  non-existent `user_id` mints a real rider account, bypassing OTP. *Why it matters:* if `JWT_SECRET` ever
  leaks, this is phantom-account injection. Even without a leak, auto-create-on-auth is the anti-pattern
  CLAUDE.md explicitly warns against ("don't fall through to create new user"). **Fix:** return 401 on
  user-not-found for the JWT path; account creation belongs only to the OTP flow.
- **`:118` — `jwt.decode(..., options={"verify_aud": False})`** disables audience checks for every caller of
  `verify_jwt_token`. A mobile (`spinr:mobile`) token is then accepted anywhere that doesn't re-check `aud`
  manually. **Fix:** pass the expected `audience=` and drop `verify_aud: False`.
- **`:256-265` — `admin-001` super-admin is built entirely from JWT claims** (email/role/modules), skipping
  the DB read, JTI denylist, and `token_version` gate that real staff go through. An unrevocable super-admin
  token class. **Fix:** route `admin-001` through the same `token_version`/JTI checks.
- **`:104` — full E.164 phone embedded as a JWT `phone` claim.** JWTs are base64, not encrypted; the number
  rides in every `Authorization` header and the `/auth/refresh` JSON body. PIPEDA forbids full phone in any
  payload that can reach logs. It is unused for authz (`user_id` is the identity anchor). **Fix:** drop it or
  use `phone_last4`.

**Token delivery** (`backend/routes/auth.py:208-220, 1055-1063`) — tokens are returned in **both** HttpOnly
cookies *and* the JSON body ("mobile reads the body"). On web that negates the cookie: one XSS reads the token
from `response.json()`. **Fix:** body-tokens for mobile only; web contexts get cookie-only.

**Dispatch supply leak** (`backend/utils/stuck_ride_sweeper.py:81,113`) — in batch dispatch a `searching` ride
has `driver_id = NULL` but several drivers marked `is_available=False` via `claim_driver_atomic`. When the
sweeper cancels a stuck ride it releases only `ride.driver_id` — the batch-claimed drivers are **never
released** and stay permanently unavailable. *Why it matters:* a real, silent driver-supply leak that degrades
match rate (KPI ≥ 85%).

**Concurrent double-dispatch** (`backend/routes/rides.py:626, 797-822`) — `match_driver_to_ride` claims
drivers but never CAS-guards the ride row before sending offers. `_dispatch_retry`, the batch-timeout handler,
and the scheduled-dispatch loop can each claim *different* drivers for the *same* ride. **Fix:** atomic ride
claim (`UPDATE ... WHERE status='searching' RETURNING *`) before fan-out.

**Insurance period misclassification** (regulatory) — CLAUDE.md mandates Period 2 begins at `driver_assigned`,
and `rides.py:3426` does fire it there on the legacy path. But **batch dispatch goes `searching → driver_accepted`
directly**, so in production Period 2 effectively starts at `driver_accepted` — a gap in SGI commercial-insurance
classification. **Fix:** emit the Period-2 transition on first offer-accept in the batch path, or document the
model change with insurance sign-off.

**SOS silent failure** (`backend/routes/rides.py:4678`) — the `safety_incidents` insert has no try/except. A
transient DB error 500s the handler and **none** of the downstream fan-out (admin WS, safety-team page,
contact SMS) runs. This is the actual P0. **Fix:** attempt WS + SMS independently of the DB write; never let
persistence failure suppress the panic fan-out.

**Money — receipt transparency** (`fare_service.py:242-248`, `routes/rides.py:492-495`) — base/distance/time
fares are bundled into a single "Ride fare" line in *both* receipt builders. CLAUDE.md's "no hidden fee /
every charge a disclosed line item" contract requires them itemized. **Fix:** split the line items.

**Money — corporate priority** (`payment_service.py:237-373`) — `settle_corporate` applies allowance → master
wallet with **no rider-wallet-first step**, over-spending corporate budget on rides the rider's own wallet
could cover. The allowance-debit → master-debit pair is also not a single atomic transaction (compensating
grant-back can itself fail). **Fix:** insert rider-wallet step; wrap debit pair in the `corporate_wallet_apply_delta`
RPC or a single SQL function.

---

## 🛡️ Error Handling & Telemetry (user UX vs admin logging)

**Good (keep):** the shared API client (`shared/api/client.ts:318-362`) normalizes every backend error shape
into a structured `SpinrApiError` with an i18n `messageKey` + `actionHint` — no raw bodies or stack traces
reach users. Sentry runs `sendDefaultPii:false`, drops console breadcrumbs, and the persisted error ring
buffer redacts GPS in URLs. This is exactly the graceful-degradation posture you want.

**Gaps:**
- **SOS partial failure is invisible** (`rides.py:4730-4752`) — if contact SMS all return `{success:False}`
  without raising, the rider gets `success:True, contacts_notified:0` and no "call them directly" warning
  (only the *exception* path warns). Surface a `notification_warning` whenever `contacts_notified == 0`.
- **`scheduled_rides.py:107-108`** detects the active-ride conflict via `str(claim_exc).lower()`, but on a
  wrapped `DatabaseError` `str(e)` is the generic "Database operation failed" sentinel — so a real conflict
  **falls through to `raise`** and logs as a dispatch failure instead of deferring. Use the `pg_error_code`
  helper (as `drivers.py:4357` does).
- **Sentry scrub coverage holes** — `sentry_scrub.py:44-50` only walks `message`/`logentry`/`exception.value`;
  it skips `event["extra"]`, `contexts`, `request.data`, breadcrumbs-data. A `logger.error(..., extra={"phone":...})`
  leaks. And the GPS regex (`ai/pii.py:21`) requires `\d{4,}` decimals, missing 2–3-decimal coords (~100m,
  still a house in Saskatoon). **Fix:** walk `extra`/`contexts`; loosen regex to `{2,}`.
- **Insurance-period write is intentionally swallowed** (`insurance_periods.py:182-197`) — sanctioned, but the
  only alerting hook is `spinr_insurance_period_write_failed_total`. **Confirm an alert is wired**, or audit
  gaps become invisible.
- **Dispute auto-resolve** (`webhooks.py:830-875`) — `charge.dispute.closed` + `won` auto-flips
  `payment_status='paid'`, which is borderline against "Support triages disputes; code must not auto-resolve."
  Consider flag-for-review instead of silent re-mark.

---

## 🐢 Performance Bottlenecks & Optimizations

- **Dispatch notify loop N+1** (`rides.py:916-1031`) — per-driver `quest_progress` query + offer-card token
  signing run **serially** for up to 10 drivers on the dispatch hot path. Directly inflates P95 dispatch
  latency (KPI < 2 s). **Fix:** batch the quest read via `.in_()`; sign tokens concurrently.
- **`complete_ride` pages up to 10k breadcrumbs inline** (`drivers.py:3915-3928`) then road-snaps and writes
  `ride_routes` before responding — fare-settlement SLA (< 1 s) is unachievable on long trips. **Fix:** move
  snapping/route-write to a background task; settle on stored fare.
- **`location-batch` double `drivers` read + inline insert** (`drivers.py:1781, 1832-1834`, insert at `:1825`)
  — second fetch is redundant (reuse row from `:1781`); the multi-row insert is synchronous to the response,
  risking the < 150 ms write SLA for large batches. **Fix:** drop the second read; buffer the insert like the
  WS path does.
- **`accept_ride` ~4 sequential reads before the CAS** (`drivers.py:3306-3408`) — burns the offer→accept KPI.
- **Admin WS fan-out unthrottled on ride status** (`socket_manager.py:404`) — scales with ride volume ×
  admin count; driver-location fan-out is throttled but status is not.

---

## 💡 Tech Stack & Architecture Recommendations

The stack (FastAPI + Supabase/RLS + Redis + Stripe, dual Fly/Railway) is sound for the scale. Gaps vs how
Uber/Lyft run the same hot paths:

1. **Introduce a real job queue** (arq or Dramatiq — async-native, Redis-backed, fits the existing Redis dep
   better than Celery). Today heavy work (breadcrumb snapping, T4A, receipt PDF, route writes) runs inline in
   request handlers or in the 16 in-process startup loops. A queue gives retries, visibility, and gets the
   < 1 s settlement SLA back. *Root cause:* inline awaits of slow I/O on user-facing paths.
2. **Transactional outbox for state-change events.** Ride state changes today must "remember" to emit a WS
   event; an outbox table drained by a worker guarantees every transition fans out exactly once and survives
   a crash between DB-write and WS-emit. This is how the big players avoid lost status updates.
3. **Settlement reconciliation loop** — the missing tail of the fare-collection P0. A replay-safe loop that
   finds `status=completed AND payment_status=pending` older than N minutes and re-drives `process_payment`.
   Mirror the existing stuck-ride sweeper recipe.
4. **Single atomic SQL function for insurance close+open** (`insurance_periods.py:116-141`) — today the close
   and the insert are two statements; a crash between them leaves a zero-open-period audit gap. One
   `SECURITY DEFINER` function eliminates it.
5. **Request-body size limits in middleware** — `location-batch` only caps *after* loading the whole client
   array into memory. Enforce a body cap upstream.
6. **Idle GC for breadcrumb buffers** (`breadcrumb_buffer.py:51`) — add a TTL sweep so a skipped WS-disconnect
   cleanup can't strand per-driver buffers.
7. **Batch Supabase reads** — several hot paths do per-row reads in loops; standardize on `.in_()`.

---

## 🛠️ Maintainability & Code Smells

- **Two divergent offer-timeout handlers** (`rides.py:1042` single vs `:1216` batch); the single one's guard
  never matches in batch mode — dead code that would double-release drivers if rewired.
- **Two completion writers with divergent fare logic** — `drivers.py::complete_ride` recomputes fare on actual
  distance; `rides.py::rider_complete_ride` (`:5110`) does not. Same trip bills differently by path.
- **Phantom legacy states** `"requested"`/`"en_route"` in `_cancellable_states` (`rides.py:4171-4178`) — not in
  the documented state set, silently accepted, contradicting the "surface unknown states loudly" contract.
- **`_f`/`_fd` float aliases** (`fare_service.py:50-57`) blur Decimal discipline; `routes/rides.py:492` does a
  Decimal→float→Decimal round-trip that can drop a cent on receipts.
- **Fragile error-code matching via `str(e)`** in multiple spots — standardize on the `pg_error_code` helper.

---

## 🧪 Testing & QA — Missing Edge Cases

Add regression tests for every live finding (CLAUDE.md requires it):
- **Settlement reconciliation:** a `completed + pending` ride gets re-driven to `paid`.
- **Concurrent double-dispatch:** two dispatch invocations on one `searching` ride → exactly one offer batch.
- **Sweeper releases batch-claimed drivers:** cancel a stuck `searching` ride → all `claim_driver_atomic`
  drivers flip back to `is_available=True`.
- **SOS DB failure:** incident insert raises → WS + SMS fan-out still attempted; response carries a warning.
- **Insurance Period 2 timing:** assert the transition fires at assignment/first-accept, not later.
- **Money branches:** surge-on-base-fare policy, corporate rider-wallet-first priority, itemized receipt lines.
- **Auth:** forged `admin-001` token rejected; JWT path returns 401 (not auto-create) on unknown `user_id`;
  cross-audience token rejected.

---

## 📈 Manager's Verdict

**Overall health: B+ / strong.** This is a mature, convention-dense codebase with genuinely good instincts —
structured user-facing error mapping, PII-scrubbed telemetry, hardware-backed token storage, atomic dispatch
claims, Decimal money helpers, Stripe idempotency, append-only insurance audit. Several backlog "P0s" are
already fixed, which says the team's remediation cadence is working.

The real risk is **not** breadth — it's a few **high-consequence, low-visibility** failure modes that won't
show up in a demo: a silent driver-supply leak in the stuck-ride sweeper, an unguarded SOS insert that can
swallow a panic event, a fare-settlement reconciliation gap that quietly leaks revenue, and a cluster of
auth-hardening items that are only dangerous if `JWT_SECRET` leaks (so secret hygiene is itself a control).

**Prioritized plan:**
1. **This week (P0):** guard the SOS insert + fan-out; release batch-claimed drivers in the sweeper; add the
   settlement reconciliation loop; itemize receipt lines.
2. **Next (P1):** auth hardening (drop `verify_aud:False`, 401-not-create on JWT path, `admin-001` through
   revocation checks, phone out of JWT); corporate rider-wallet-first; Sentry scrub `extra`/GPS-regex.
3. **Architecture (P2):** introduce arq/Dramatiq + transactional outbox; move heavy completion work off the
   request path; single-statement insurance close+open.

vs **Uber/Lyft:** the domain modeling (insurance periods, surge cap, regulatory retention) is arguably *more*
disciplined than a generic clone. Where they're ahead is operational plumbing — a job queue and an event
outbox on the dispatch/settlement hot paths. Closing that gap is the highest-leverage architectural work.
