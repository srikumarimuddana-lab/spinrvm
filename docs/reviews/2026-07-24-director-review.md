# Spinr — Engineering Director Code & Architecture Review

**Date:** 2026-07-24 · **Type:** Read-only teardown (no code modified) · **Branch:** `claude/epic-planck-alt9ts`
**Benchmark:** Uber / Lyft engineering practice
**Method:** Six parallel domain sweeps — security/auth, payments/money, dispatch/realtime, error-handling/telemetry, frontend surfaces, architecture/tooling/testing — cross-checked against `CLAUDE.md`, `ACTION_ITEMS.md`, and the current sprint doc.

> **Framing.** Spinr is a **mature, incident-hardened codebase**, not a rough prototype. The current sprint's six P0s (HttpOnly tokens, admin TTL, first-rating crash, fare-collection state mismatch, GPS OOM, SOS silent failure) are all **shipped and regression-pinned**. This review therefore reports *residual* and *newly-surfaced* findings, and explicitly credits the strong foundations so the signal is honest. The bar being applied is "public-launch and 10×-scale readiness for a payments + safety platform," which is a high bar.

---

## 🚨 Critical Issues & Security Flaws

### C1 — [BLOCKER · PIPEDA] Raw GPS coordinates and exact addresses logged on every address search
`backend/routes/maps_proxy.py:114-120` and `:147-150` *(confirmed firsthand)*
- `places_autocomplete` logs at **INFO on every call**: `input=%r` (the raw text the rider is typing — often a full address), `location=%s` (the raw `"lat,lng"` origin string), and separately `predictions[0].description[:60]` (Google's top formatted result — "123 Main Street, Saskatoon, SK" fits well under 60 chars).
- **Why it's a problem:** `CLAUDE.md` (PIPEDA section) forbids raw GPS and exact addresses in logs — "log geohashed area at most / city/area only." The Sentry scrubber does **not** save this: it only runs on Sentry-captured events (not raw stdout / log aggregator), and its coord regex requires ≥4 decimals on both lat and lng, so a `52.13,-106.67` string slips past anyway.
- **Impact:** Continuous PII leakage into log storage from the single most-used rider action. A log-store exposure becomes a reportable privacy breach (Privacy Commissioner, 72h clock).
- **Fix (conceptual):** Drop `input`/`location`/`description` from these log lines, or geohash `location` and route the result through the existing `area_only()` helper (`utils/pii.py`). Add a `geohash()` helper to `utils/pii.py` (none exists yet). ~30 min, one file.

### C2 — [HIGH · fail-open financial control] Corporate policy enforcement fails OPEN on DB error
`backend/services/corporate_policy_service.py:113-135` (also `:221`, `:243`)
- On any exception fetching the policy/allowance, the code does `logger.warning(... "treating as no policy")` and **continues as though no spend controls exist** — payment-source restriction, time-window, geofence, and allowance are all bypassed.
- **Why it's a problem:** This is the exact anti-pattern `CLAUDE.md` forbids ("never `logger.warning` and continue on a DB error; fail loud"). A Supabase/PostGIS brownout silently disables all corporate spend authorization, and the WARNING-level log makes a mass-bypass event look like normal noise.
- **Impact:** Unauthorized rides charged to corporate accounts during any DB degradation; no incident signal.
- **Fix:** Fail **closed** — propagate `DatabaseError` (or `logger.error` + reject the ride) instead of defaulting to "no policy."

### C3 — [HIGH · compliance correctness] Insurance-period transition is non-atomic with a swallowed failure
`backend/utils/insurance_periods.py:116-176`
- `record_period_transition` **closes the prior period (step 1) then inserts the new one (step 2) non-atomically**; if the insert fails with anything but a unique violation, the outer `except` at `:182` swallows it, leaving the driver with the previous period *closed and no open period* while mid-trip. Periods are derived by fire-and-forget calls at each ride transition and never reconciled against `rides`.
- **Why it's a problem:** SGI / Saskatchewan Transportation Act audits depend on a gapless period trail (`CLAUDE.md` insurance-periods invariants: "append-only, Period 3 requires a `ride_id`"). A swallowed failure produces an audit trail that provably diverges from reality.
- **Impact:** Missing/gapped commercial-insurance audit rows for a driver demonstrably on a trip — a regulatory and insurance-liability exposure.
- **Fix:** Wrap close+open in a single Postgres function/transaction (RPC); add a periodic reconciler that rebuilds expected open-periods from active `rides` (the code comments claim this backfill exists — it does not run as a loop).

### C4 — [MEDIUM · latent user-facing leak] Two competing base-`Exception` handlers; safety depends on registration order
`backend/core/middleware.py:642-680` vs `backend/utils/error_handling.py:884-889`
- `cors_exception_handler` returns raw `exc.detail` for anything with `status_code`/`detail` with **no 5xx sanitization**. It is currently dead only because `register_exception_handlers` runs *after* `init_middleware` in `server.py` (last-write-wins). Reorder those two calls and raw 5xx details (Stripe IDs, Supabase constraint names, JWT errors) flow to clients again.
- **Fix:** Delete `cors_exception_handler`; fold any missing CORS/security-header behavior into the single sanitizing `general_exception_handler`.

### C5 — [MEDIUM · auth policy gaps]
- `admin/auth.py:722-723` — self-service `/admin/auth/change-password` enforces only `len >= 12`, bypassing the 20-char complexity+blacklist `validate_admin_password()` used everywhere else. An attacker who phished a password can rotate *down* to a weak value to lock in persistence. → Call `validate_admin_password()` here too.
- `admin/auth.py:329-361` — the `admin-001` env-var super-admin (`super_admin`, `ALL_MODULES`) is **unconditionally MFA-exempt** while every lesser staff account is forced into MFA. A leaked `ADMIN_PASSWORD` is one factor from full takeover. → Give admin-001 a TOTP secret from the secret manager, or retire it in favor of a first-class MFA'd `super_admin` staff row.

### C6 — [MEDIUM · defense-in-depth] RLS not enabled on ~15 user-data tables
`backend/supabase_rls.sql` + migration history
- `refresh_tokens`, `emergency_contacts`, `disputes`, `documents` (driver ID/background docs), `corporate_accounts`, `corporate_rides`, `audit_logs`, `staff`, `notifications`, `legal_documents`, `promotions`, `ride_messages`, etc. have no `ENABLE ROW LEVEL SECURITY`. Only 10 tables are covered.
- **Mitigated today** because the backend is the sole DB client (service role) and no frontend holds a Supabase anon key (verified). It becomes a live breach the moment anyone wires an anon key for a "fast read." Violates `CLAUDE.md` ("every new user-data table ships RLS in the same migration"). → Backfill RLS starting with `refresh_tokens` and `emergency_contacts`.

**Credit (security is a genuine strength):** JWT trust model implemented exactly as documented (rider/driver role always re-read from DB, admin trust centralized in one `_verify_admin_payload`); OTP handling textbook (SHA-256 + `compare_digest`, 5/hr → 24h lockout, fail-closed on Redis outage); refresh-token reuse detection implements the full OAuth2 BCP §4.14.2 cascade; `_validate_production_config()` fails hard at startup on weak secrets; CORS is an explicit allowlist with a hard `RuntimeError` on `*` in prod.

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**User-facing side is well-defended.** 5xx responses are sanitized to "Internal server error" behind an `ERR_*` allowlist; every response carries a `request_id` echoed in body + `X-Request-ID`; the mobile client strips Axios generics and maps numeric `error.code` → friendly localized titles (`shared/errors/errorPresentation.ts`). No raw stack traces or backend bodies reach rider/driver toasts.

**Residual leaks (small):**
- `shared/components/ErrorBoundary.tsx:38-55` — renders the **full JS/component stack to the user in production** (deliberate, for screenshot-based diagnosis). Uber/Lyft never do this. → Gate the stack behind `__DEV__`; ship it to Sentry (already wired), show only a branded fallback.
- `admin/driver_import.py:67,90` — returns raw `str(e)` from `ValueError`/`RuntimeError` on 4xx (admin-only, minor).
- `admin-dashboard/src/lib/api.ts:169-175` — surfaces backend `detail` verbatim in admin toasts (no sanitizing ladder like the mobile client has).

**Admin-side (observability) gaps:**
- **Unhandled 500s / DB errors reach Sentry as flat `capture_message`, not `capture_exception`** (`error_handling.py:632/739/859` use `logger.opt(raw=True)`, which nulls the exception object → `server.py:482-486` takes the message branch). Distinct root causes collapse into one bucket with no stack grouping. → Route these through `logger.opt(exception=exc)` or call `capture_exception` directly.
- **Thin Sentry tags** — `tags_from_log_extra` only lifts what the call site put in `extra={}`; the central DB-error log (`repositories/_base.py:323`) and the boundary handlers pass none, so DB incidents land with only `surface=backend` and can't be sliced by `domain`/`ride_id`. → Add `extra={"domain": ...}` at those sites.
- **Metrics are per-process, in-memory** (`utils/metrics.py` explicitly does not aggregate across replicas, lost on restart) — a compat shim, not a scraped TSDB.

**Credit:** the DB layer surfaces loudly and correctly (`logger.error` + `DatabaseError(details={"original":...})` + circuit breaker that treats 23505 bursts as success to avoid false-open); Prometheus naming is consistent (`spinr_<domain>_<metric>_<unit>`) with SLA-tuned histogram buckets; Sentry scrubs phone/email/GPS/postal in `before_send`/`before_breadcrumb` with `send_default_pii=False`.

---

## 🐢 Performance Bottlenecks & Optimizations

1. **[HIGH] Synchronous REST-over-Postgres behind a 64-thread pool is the core scaling ceiling.** Every DB call is a PostgREST HTTP/1.1 request on `ThreadPoolExecutor(max_workers=64)` (`repositories/_base.py`, `supabase_client.py`). Per-replica concurrency is capped at 64 threads, each paying HTTP framing + JSON (de)serialize under the GIL + un-prepared query planning. CPU saturates before Postgres does; the dispatch (<2s) and fare (<300ms) SLAs break here first. → Introduce **asyncpg** (native async, binary protocol, prepared statements) on the hot money/dispatch/location paths, pointed at Supabase's **Supavisor/PgBouncer pooler (6543, transaction mode)**; keep supabase-py REST for admin CRUD. Expected 5–10× per-replica throughput and removal of the HTTP/1.1 workaround.

2. **[HIGH] Surge supply count is O(areas × entire-online-fleet) in Python.** `surge_engine.py:161-196` fetches up to 5000 driver rows with no service-area predicate, then runs `point_in_polygon` in Python *per area*, every 2 min — and `get_surge_status` re-runs the same full-fleet scan **synchronously on every admin dashboard poll**. The 5000 cap, if hit, over-states a regulated price. → Make the already-written PostGIS spatial count (`drivers_available_in_polygon` RPC) the default instead of gating it behind `SURGE_SPATIAL_COUNT=off`; serve `get_surge_status` from last-persisted values.

3. **[MEDIUM] Dispatch candidate pool is an unordered `LIMIT 500` inside a bounding box** (`routes/rides/matching.py:270-291`, cascade `:547`). In a dense downtown at peak, the nearest driver can be dropped. The PostGIS GiST index (migration 170) exists but isn't on the dispatch path. → Route candidate selection through a k-nearest RPC ordered by distance.

4. **[MEDIUM] Every ride state transition broadcasts to all admin sockets fleet-wide** (`socket_manager.py:443-446`) — O(rides × admins). → Admin subscription protocol scoped by service-area / bounding box.

5. **[MEDIUM-LOW] Hot-path micro-costs on the <2s dispatch budget:** same `service_areas` row fetched 4+ times per dispatch attempt (`dispatch_service.py:270`, `matching.py:381/477/511`); serial per-driver `quest_progress` queries inside the notify loop (`matching.py:795-838`); serial per-ride re-dispatch reads in `offer_expiry_reaper.py:117-123`. → Fetch area once and thread it; batch quest progress in one IN-query.

6. **[MEDIUM] Admin analytics uncached** (`ACTION_ITEMS.md` D7 — "~98% DB-load reduction available"); row cache is 30s TTL only. Dashboard aggregates hit Postgres every load, competing with the trip path for the same 64 threads. → 5-min Redis cache on aggregates; consider a read replica for reporting.

**Credit:** the two hardest ride-hailing correctness problems are solved correctly — the **acceptance race** (atomic conditional `UPDATE` on `status`+`driver_id`, `ride_flow.py:206-261`) and **offer-timeout replay-safety across replicas/restarts** (single atomic `pending→expired` claim gating all side-effects). Reapers are leader-locked + jittered; presence fail-open is nuanced (distinguishes "Redis down" from "authoritatively empty"); GPS breadcrumb buffers are bounded (`_MAX_POINTS=10`, evicted on disconnect).

---

## 💡 Tech Stack & Architecture Recommendations

| Gap | Why it matters | Recommendation |
|---|---|---|
| **No async Postgres driver** | See Perf #1 — the throughput ceiling | **asyncpg + Supavisor pooler** on hot paths |
| **No durable job queue** | Out-of-band work (SMS/receipts/push/reconcile) runs as fire-and-forget `asyncio.create_task` + 16 per-replica loops; a task lost to a restart/OOM is gone — no retry, DLQ, or backpressure | **arq/Celery on Redis** short-term (or SQS/CloudTasks); move the 16 loops onto one scheduled/leader-elected worker |
| **No staging environment** | `main` deploys straight to prod on both providers; migration rehearsal, load test (`loadtest/locustfile.py` never run), DAST, a11y, and PITR/failover drills are all blocked on this one gap | Stand up a staging Fly app + throwaway Supabase — prereq for everything else |
| **No distributed tracing** | `X-Request-ID` correlates logs but no span propagation across API → PostgREST → Stripe/Twilio/Maps; multi-hop latency root-causing is guesswork | **OpenTelemetry** auto-instrumentation (FastAPI/httpx/redis) → Tempo/Honeycomb/Datadog — cheap given request-ID plumbing exists |
| **No feature-flag / kill-switch** | Config lives in `app_settings` but no boolean kill switches on surge/dispatch/promo/corporate billing; quarantining a runaway subsystem needs a deploy | Flags at the top of each loop/path + admin toggles; Unleash/Flagsmith if usage grows |
| **No external synthetic monitoring** | Nothing probes from outside; MTTD for a full outage = "first angry user" | Checkly/Grafana Synthetic on `/health` + auth + fare-estimate → PagerDuty |
| **Redis in-process fallback degrades silently** | If `REDIS_URL` is unset in prod, cache/rate-limit/presence/idempotency/pubsub silently become per-replica → cross-replica correctness lost | **Fail fast on boot** when `ENV=production` and `REDIS_URL` absent; verify on both providers |
| **Manual DNS-swap failover, never drilled** | Fly↔Railway failover is a Cloudflare CNAME edit, no LB, standby "drifts silently" on env vars; RTO = DNS TTL + human | Run the C1 drill and record real timings; long-term, health-checked Cloudflare LB |
| **Heavy mixed-purpose image** | Bundles pandas/numpy/boto3 + **three LLM SDKs**; `google-generativeai 0.8.6` is a deprecated SDK line; `psycopg2-binary` present but unused via PostgREST | Split ML deps into a worker image; migrate to `google-genai`; drop `psycopg2-binary` (or adopt deliberately per Perf #1) |

**Honest scale verdict:** Spinr is a well-disciplined **single-region monolith**, which is the *correct* choice for a Saskatchewan-first launch — the team has resisted premature distribution while still building real circuit breakers, idempotent replay-safe loops, and a strong CI/security posture. At 10× the DB layer (Perf #1) breaks first; then the per-replica-loop / fire-and-forget model and per-replica Redis fallbacks turn into correctness bugs (double charges, dropped OTPs). At 100× it is categorically different from Uber/Lyft (no Kafka, no geo-sharded dispatch, no cell isolation, DNS-swap ≠ multi-region HA) — but none of that is a criticism at this stage; it is the correct backlog order.

---

## 🛠️ Maintainability & Code Smells

- **Documentation drift from implementation (governance risk).** Several controls described in `CLAUDE.md`/domain docs don't match the running code: the pre-commit "float blocker" is actually a **non-blocking, over-broad WARNING** not scoped to fare files (`.claude/hooks/pre-commit:91-98`); the allowance-reset "replay via `auto_approved_this_period` flag" actually uses CAS-on-`period_end` (stronger, but different); the "4-tier payment-source cascade" is really an explicit method pick at booking with a cascade only *inside* `company_allowance`. For a regulator/auditor reading `CLAUDE.md` as source-of-truth, described controls not matching reality is a governance risk even when the code is safe. → Reconcile the docs.
- **Stripe API-version pin drift.** `routes/wallet.py:200` uses `stripe_version=stripe.api_version` where the sibling `payments.py:987-995` was explicitly fixed to use the pinned `STRIPE_API_VERSION` (with a comment explaining the cold-start fallback bug). → Port the fix; extend `test_ephemeral_key_signature.py` to assert the literal pin.
- **`_f()` (Decimal→float) crosses into money-moving RPCs**, not just display (`payment_service.py:423/432/446`) — low practical risk (values pre-quantized) but a stated-invariant violation. → Pass the Decimal directly.
- **Dead/duplicated config landmines:** `core/csrf.py:11-27` defines exemption sets that are never read (real enforcement is a second list in `middleware.py`); two daily Stripe reconciliation loops with overlapping scope (`reconciliation.py` + `stripe_reconcile.py`); dual dispatch models coexist (legacy single-offer vs batch), leaving `stuck_ride_sweeper`'s driver-release branch effectively dead for batch rides; stale comments referencing the retired presence sweeper. → Each is a "future contributor edits the wrong one" trap; consolidate to a single source of truth.
- **`print()` in `services/driver_import_service.py:825-836`** bypasses structured logging/Sentry (acceptable as a CLI helper; a gap if ever called in-request).

---

## 🧪 Testing & QA (missing edge cases)

- **[HIGH] Per-module coverage floors are not enforced.** `CLAUDE.md` mandates ≥90% on `payments.py`/`fare_service.py`/`crypto.py` and ≥80% on `rides.py`/`dispatch_service.py`, but `pytest.ini` enforces only a **flat 60% global floor**. A refactor can gut payment-branch coverage while CI stays green (tracked as `ACTION_ITEMS.md` A1, "single biggest remaining gap"). → Add per-path `--fail-under` gates keyed to the CLAUDE.md table; ratchet global 60→70→80; add `.github/CODEOWNERS` so `payments*`/`fare*`/`migrations/` can't merge on a drive-by approval.
- **[MEDIUM] No consumer-facing E2E or contract tests.** Playwright covers admin-dashboard only; the rider/driver RN flows have no e2e safety net (`.maestro/` exists but isn't wired into CI), and there are **no consumer↔provider contract tests** between the apps and the API — a schema change can silently break old app binaries (compounded by the missing forced-upgrade gate). → **Pact** contract tests; Detox/Maestro for RN flows; `@axe-core/playwright` for the WCAG 2.1 AA regulatory mandate (axe is in devDeps but never run).
- **[MEDIUM] No DAST / pre-launch pentest.** SAST/Semgrep/dep-audit/secrets/container/license all run in CI (impressive) but nothing exercises the *running* app. → OWASP ZAP baseline against staging + an external pentest (E6).
- **Missing regression pins for the findings above:** the Stripe-version drift (assert the literal pin), the corporate-policy fail-closed path (simulate a DB error → expect rejection), and the insurance-period atomicity/reconciler.

---

## 📈 Manager's Verdict — overall code health

**Grade: strong, launch-capable with a short must-fix list. "Excellent early-Series-A" maturity, well above typical seed/regional hygiene.**

This is a codebase built by a team that has clearly learned from its own incidents — the comments cite specific bug IDs, the hardest correctness problems (payment idempotency, acceptance race, replay-safe loops, refresh-token reuse cascade) are solved *correctly*, PII minimization is baked in at the type level rather than bolted on after a breach, and there is an honest, prioritized self-audit backlog. On the client tier, secure token storage is at or above Uber/Lyft parity. That maturity is the headline.

The gap to "Uber/Lyft-grade" is **breadth and automation, not correctness of the core**: no async DB driver, no durable queue, no staging environment, no distributed tracing, per-replica metrics, and unenforced coverage/a11y/DAST. The near-term risk is concentrated in a handful of items where a *degraded-dependency* path does the wrong thing quietly — the PIPEDA log leak (C1), corporate policy failing open (C2), insurance-period audit gaps (C3), and Redis silently going per-replica. Those are the ones to fix before a public launch, because they fail silently and touch privacy, money, and regulatory audit respectively.

### Prioritized remediation plan

**P0 — before public launch (each small, high-leverage):**
1. C1 — strip/geohash PII from `maps_proxy.py` logs *(one file, ~30 min)*
2. C2 — corporate policy fail **closed** on DB error *(one service)*
3. C3 — atomic insurance-period transition (RPC) + reconciler loop *(one util + one migration)*
4. C4 — delete the shadow `cors_exception_handler`
5. C5 — MFA for admin-001 + enforce full password policy on self-service change

**P1 — launch-hardening:**
6. asyncpg + Supavisor pooler on hot paths (Perf #1)
7. Enforce per-module coverage floors + CODEOWNERS (Test #1)
8. Stand up staging env (unblocks load test / DAST / a11y / drills)
9. PostGIS spatial surge count on by default; cache `get_surge_status` (Perf #2)
10. Backfill RLS on `refresh_tokens`, `emergency_contacts` first (C6)
11. Fail-fast on missing `REDIS_URL` in production

**P2 — scale + observability:**
12. Durable job queue (arq/Celery); consolidate the 16 loops onto one worker
13. OpenTelemetry tracing; scraped multi-replica metrics; SLO burn-rate alerting
14. k-nearest dispatch RPC + area-scoped admin fan-out (Perf #3, #4)
15. Feature-flag / kill-switch layer; external synthetic monitoring; forced-upgrade gate
16. Reconcile CLAUDE.md docs with implementation; consolidate dead/duplicated config paths

---
*Generated by an automated read-only review sweep (6 parallel domain agents). No source files were modified. File:line references are current as of the branch head above.*
