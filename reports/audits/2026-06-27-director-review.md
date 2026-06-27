# Spinr — Director-Level Code & Architecture Review

_Read-only teardown. 2026-06-27. Branch `claude/epic-planck-xztv1l`. HEAD `29dac32`._

**Method.** Four parallel read-only audits (security/PII, money/payments, error-handling+performance, testing+tech-stack+maintainability) across the 155k-LOC backend and the four client surfaces, anchored against `ACTION_ITEMS.md` and the current sprint. Headline findings were independently re-verified at file:line before inclusion. **One agent "blocker" was discarded as a false positive** (see note in §1). Nothing in this document modifies code — it is a diagnosis + prioritized plan.

**Bottom line.** This is a genuinely mature, well-governed codebase — closer to a Series-A rideshare platform than a prototype. Error handling, the money core (Decimal discipline, Stripe idempotency, 0% commission invariant, 2.5× surge cap), and admin RBAC are hardened and largely already verified clean. The real gaps are (a) a small number of concrete security/RLS holes, (b) precision/transparency hygiene on the receipt/tax path, and (c) operational-maturity tooling that Uber/Lyft have and Spinr does not yet (staging, kill switches, synthetic monitoring, per-path coverage enforcement). None of it is architecturally broken.

---

## 🚨 Critical Issues & Security Flaws

| # | Severity | Finding | Evidence | Why it matters |
|---|---|---|---|---|
| S1 | **High** | `cloud_messages` ships an `… FOR ALL TO authenticated` RLS policy for admins. No later migration revokes it (siblings `audit_logs`/`disputes` were fixed in migrations 51 & 142; this one was missed). | `backend/migrations/06_cloud_messaging.sql:75-84` (verified; no superseding policy in 99/193) | A compromised admin credential can INSERT/UPDATE/**DELETE** push-notification records **directly via the Supabase PostgREST endpoint**, bypassing the backend and its audit trail — arbitrary FCM pushes to any user, or silent deletion of records. |
| S2 | **Med-High** | `push_tokens` also uses `FOR ALL TO authenticated` (ownership predicate is correct, but the verb set is not enumerated). | `backend/migrations/06_cloud_messaging.sql:97-100` | A user can DELETE their own push-token rows directly, silently disabling **SOS / safety** push delivery. Violates the house rule "never `FOR ALL` on user-writable tables." |
| S3 | **Med-High** | `/admin/auth/logout-all` and `/admin/auth/change-password` decode the admin JWT manually (`jwt.decode`) instead of `Depends(get_admin_user)`, skipping the **token_version (revocation)** and **30-min idle-timeout** checks. | `backend/routes/admin/auth.py:555,570` and `:632,658` | A **force-revoked** admin session stays usable on these two endpoints for up to the 1-hour token TTL. `change-password` is partially mitigated — it requires the current password — but `logout-all` is not. |
| S4 | **Medium** | Mixpanel analytics logs and forwards the raw `properties` dict with no PIPEDA field allowlist; `track_ride_requested`/`track_ride_completed` pass ride data through unfiltered. | `backend/utils/analytics.py:51,73,96,331,335` | If any caller ever includes email / full name / phone / exact address, PII reaches logs (dev/staging) and a third party (prod). PIPEDA exposure; the failure mode is a future careless caller, not today's code. |
| S5 | Info / defense-in-depth | `verify_jwt_token` decodes with `options={"verify_aud": False}` on the rider/driver path. | `backend/dependencies/__init__.py:118` | Mitigated in practice — role is always re-read from the DB (`:393`) — but audience should still be validated at decode for defense-in-depth. |

> **Discarded false positive:** an audit flagged `payment_retry.py:337` as omitting `api_key` on `PaymentIntent.confirm`. Verified incorrect — `api_key=stripe_secret` is passed at `:339`. No action.

**Confirmed already-solid** (sampled and clean): production fail-fast on weak `JWT_SECRET`/default `ADMIN_PASSWORD`/placeholder Supabase creds; no wildcard CORS in prod (raises at boot); full security-header suite + CSRF double-submit; OTP SHA-256 + 5-fail Redis lockout; refresh-token rotation with reuse-detection cascade; `claim_stripe_event` gate before any webhook processing; router-level `Depends(get_admin_user)` across all 14 admin sub-routers.

---

## 🛡️ Error Handling & Telemetry (user experience vs admin logging)

**Verdict: the strongest area of the codebase — compliant with the CLAUDE.md mandate.** This is where many teams are weakest; Spinr is ahead.

- **No raw errors leak to users.** A global handler (`utils/error_handling.py`) catches unhandled exceptions, logs full context server-side, and returns clean JSON. 5xx bodies are sanitized via a sentinel (`ERR_*`) allowlist — `raise HTTPException(500, str(stripe_error))` is caught and replaced with "Internal server error," not leaked (`error_handling.py` ~709-781, ~840-890).
- **DB errors preserve root cause for admins** — `DatabaseError` keeps the original Postgres error in `details["original"]`, per the mandate (`error_handling.py` ~531-546).
- **PII-safe telemetry.** `sentry_scrub.py` strips PII and never drops events; `domain`/`surface`/`ride_id` tags are promoted from `extra={}` for triage; `X-Request-ID` correlates client → log → Sentry end-to-end (`core/middleware.py` ~180-197).
- Soft-fail paths that were checked (reverse-geocode cache miss, optional venue lookup, Redis-presence → DB fallback) are **correct** graceful degradation on non-critical operations, with logging — not silent swallows.

**Gap:** the analytics PII allowlist (S4) is the one place telemetry hygiene is enforced by convention rather than code.

---

## 🐢 Performance Bottlenecks & Optimizations

| Severity | Bottleneck | Evidence | SLA at risk / fix |
|---|---|---|---|
| Medium | Inline `stripe.PaymentIntent.create` in the payment-confirm handler (synchronous Stripe round-trip, ~200-400 ms, p99 >1 s on jitter). | `backend/routes/payments.py:220-224` | User waits on Stripe at confirm. Idempotency key already present, so the charge is safe to background — return `client_secret` early and settle async. (Note: this is the confirm path, not the <300 ms fare-estimate path; severity is UX, not the estimate SLA.) |
| Medium | Surge engine fetches up to ~5k drivers then does **Python point-in-polygon per driver**, every 2 min. PostGIS RPC is built but flag-gated off. | `backend/utils/surge_engine.py:160-195` | Surge price lags real demand as driver count grows. Activate `_SURGE_SPATIAL_COUNT` / migration-170 RPC when any area approaches 500 online (`ACTION_ITEMS` D1). |
| Low-Med | Admin analytics renders via 4-5 sequential chunked queries (~2 s for 50 drivers). Chunking is correct (no unbounded `IN()`); the round-trips are the cost. | `backend/routes/admin/analytics.py:42-54` | Collapse to one server-side join/RPC; add the 5-min Redis cache (`ACTION_ITEMS` D7). |
| — | **Driver location hot path is well-built** — 5 s active-ride cache, 100 m ETA movement gate, breadcrumb batching (10 pts/10 s), flush on disconnect. | `utils/breadcrumbs.py`, `utils/maps_eta.py` | No action; this was a prior fix and it holds. |

**Architectural performance smell (see §"Tech Stack"):** every DB call is a synchronous `supabase-py` call offloaded to a fixed thread pool (`repositories/_base.py`). Under a dispatch spike this is a thread-pool-starvation ceiling, not an I/O-bound async path.

---

## 💡 Tech Stack & Architecture Recommendations

**Current stack is reasonable** (FastAPI 0.136, Supabase/Postgres+RLS, Redis, Stripe, Sentry, loguru, Prometheus metrics, Fly+Railway failover via CNAME). Gaps, highest-leverage first:

1. **DB driver model.** Synchronous `supabase-py` wrapped in `run_sync()` over a thread pool defeats the async I/O model and caps concurrency at the pool size. *Why it matters:* thread starvation under load. *How:* longer-term, evaluate a native async path (`asyncpg`/SQLAlchemy 2.x asyncio) for the hot dispatch/ride queries; near-term, make the pool size load-aware and instrument its saturation.
2. **No staging environment** (`E1`). `main` → prod (Fly + Railway) with nothing between. *Why:* blocks safe migration rehearsal, load testing (`E2`, harness already built), synthetic monitoring (`E4`), and backup-restore drills (`E7`). *How:* throwaway Supabase project (ca-central-1) + Fly staging app behind a `staging` branch. This single item unblocks four others.
3. **No feature flags / kill switches** (`E5`). `app_settings` holds config but there's no documented switch to disable surge, scheduled dispatch, promo redemption, or corporate billing mid-incident. *How:* boolean flags checked at the top of each risky loop + admin toggles → seconds-to-disable without a deploy.
4. **No synthetic monitoring** (`E4`). A total outage is currently discovered by users. *How:* external prober (Checkly/UptimeRobot) on `/health` + auth + fare-estimate every minute → PagerDuty, thresholds tied to the CLAUDE.md SLA table.
5. **No forced mobile-upgrade gate** (`E3`). Old binaries will eventually hit changed APIs; impossible to retrofit. *How:* `min_supported_version` in `app_settings` + version header + 426 response + "update required" screen.
6. **No distributed tracing** (OTel). Acceptable for a single service today; revisit only if multi-replica latency debugging gets painful (`ACTION_ITEMS` D2).
7. **No CODEOWNERS / DAST / license scan** (`E6`, `E8`, `E10`). SAST (Semgrep) runs in CI; a payments+PII platform should add an OWASP-ZAP baseline against staging, route `payments*`/`fare*`/`migrations/` to designated reviewers, and fail CI on GPL/AGPL in shipped surfaces.

---

## 🛠️ Maintainability & Code Smells

- **God files.** `routes/drivers.py` (7,833 LOC) and `routes/rides.py` (6,056 LOC) dominate; `admin/rides.py` (3,341), `admin/drivers.py` (2,405), `features.py` (1,681) follow. *Why:* grep-and-read cost, merge-conflict surface, review fatigue on the two most safety-critical files. *How:* split `rides.py` into dispatch / offer / state-machine modules and `drivers.py` into onboarding / availability / documents — mechanical, high readability ROI.
- **Money helper discipline is good but leaks at the edges.** Core `calculate_fare` is Decimal-clean end-to-end, but `_f()`/`float()`/`round()` appear at several persistence/response boundaries (see Money section) where `str(Decimal)` would be safer. The pre-commit float hook guards fare code; these are display/summary helpers it doesn't catch.
- **Intentional dual-import `try/except ImportError`** in every module is unusual but documented and justified — leave it.
- **16 background loops on every replica.** Well-documented replay-safety contract (atomic claims / claim flags / idempotency keys). This is the highest-risk maintainability area: one missing flag on a *new* loop = duplicate charges/notifications. Keep the "new loop must be replay-safe" review gate strict.
- **253 migrations, raw-SQL runner, historical duplicate prefixes** handled by full-filename idempotency. Works, but there's no schema-drift detection or per-migration down-script. Acceptable; document rollbacks in-file as the convention already requires.

---

## 🧪 Testing & QA (missing edge cases)

- **Coverage enforcement is the single biggest gap** (`ACTION_ITEMS` A1). Global floor is **60%** (`backend/pytest.ini`) vs the mandated **≥90% payments/fare, ≥80% rides/dispatch**. Tests exist (`test_coverage_payments.py`, `test_coverage_rides.py` ~3,260 LOC) but **CI does not block** a per-path regression. A bad commit can quietly weaken money-path testing and ship.
- **Thin state-machine coverage.** The 6-state / 12-transition ride machine has only a couple of guard-clause tests; no full-cycle test (`scheduled → … → completed`) and the edge cases — offer-timeout race, double-accept, cancel-after-`in_progress` — are scattered or absent. Every transition should have a case in `test_ride_state_machine.py` per the convention.
- **Stripe webhooks:** 11 handled event types, but no test asserting each type's handler path (`charge.dispute.created`, `payout.failed`, subscription state changes uncovered).
- **Frontend is the weakest tier.** Rider app: near-zero tests. Driver app: some hook/store tests. Admin: 5 unit + 5 Playwright E2E, and **E2E is `continue-on-error` (non-blocking) on PRs**. **a11y/axe is in deps but not wired into CI** despite the WCAG 2.1 AA regulatory mandate (`E11`).
- **Post-deploy smoke test** exists but is shallow and `main`-only (`A2`) — checks `/health` + auth-401 + fare-401 but doesn't re-verify deploy success.

---

## 💰 Money & Payments (regulatory precision)

The **core is clean and largely verified**: Decimal-only `calculate_fare`; `dollars_to_cents` quantizes (no `int(x*100)` truncation); `claim_stripe_event` idempotency before business logic; explicit `cad` currency; **2.5× surge cap enforced** with corporate + scheduled-ride exclusion; **0% commission invariant** holds (admin_earnings = booking + airport fee only); PCI perimeter actively rejects raw card fields. The findings below are **precision/transparency hygiene**, not money-loss bugs:

| Severity | Finding | Evidence | Why |
|---|---|---|---|
| Medium | GST/PST amounts persisted to `rides.tax_breakdown` as **`float`**; the persisted row is the CRA receipt document. | `backend/features.py:859-878` | Float drift can render a tax line a penny off what was charged → CRA reconciliation discrepancy. Store Decimal-as-string; `float()` only at JSON egress. |
| Medium | Receipt **bundles base + distance + time** into one "Ride fare" line. | `backend/services/fare_service.py:243-250` | Transparency obligation expects per-component disclosure; a single line is arguable "hidden" per-km/per-min pricing. Emit three labeled lines. |
| Medium | Stripe webhook stamps `processed_at` at the **end** of the dispatch block, not immediately after `claim_stripe_event`. | `backend/routes/webhooks.py` (~1361) | A 5xx between side-effect and stamp lets Stripe retry re-execute. Stamp right after claim, and guard ride updates on current status. |
| Low-Med | Driver earnings summary uses naked `float()`/`round()` for `total_earned`, `tax_amount_total`. | `backend/routes/rides.py:3759-3789` | Feeds the driver's income record → T4A; cents of drift across thousands of rides. Keep Decimal through the sum. |
| Low | No guard rejecting an `sk_live_*` key when `ENV != production`. | Stripe key load path | A mis-configured staging could make real charges. Add a startup assertion. |

---

## 📈 Manager's Verdict — overall code health

**Grade: B+ / strong. Production-capable, not yet "public-launch hardened."**

What's impressive for the stage: disciplined money core, mature error-handling and telemetry, a real RLS posture with a *track record of fixing* `FOR ALL` policies (51, 142), refresh-token reuse detection, MFA-enforced admin, and a meticulously maintained backlog. The team clearly knows where its gaps are — most of this review's operational findings are already enumerated in `ACTION_ITEMS.md`, which is itself a maturity signal.

Where it trails Uber/Lyft is **operational safety nets**, not product or correctness: no staging, no kill switches, no external "is the platform up?" probe, no enforced coverage on the money paths, and a frontend test tier that's thin enough to be a real launch risk. Combined with two concrete RLS holes (S1/S2) and an admin-revocation gap (S3), these are the things that turn a normal incident into a bad one.

**Recommended sequencing (do in this order):**

1. **This week — close the verified security holes.** S1 + S2 (new migration: drop the two `FOR ALL` policies, enumerate verbs, REVOKE writes) and S3 (swap manual `jwt.decode` for `Depends(get_admin_user)` on the two admin endpoints). Small, surgical, each with a regression test.
2. **Pre-launch gate — enforce what's already mandated.** A1 per-path coverage floors in CI (ratchet, don't big-bang) + flesh out the post-deploy smoke test (A2). Add the analytics PII allowlist (S4).
3. **Operational maturity — the Uber/Lyft delta.** Staging env (E1) first (it unblocks E2/E4/E7), then kill switches (E5) and synthetic monitoring (E4). Forced-upgrade gate (E3) is cheap-now/impossible-later — do it before the app base ages.
4. **Hygiene, ongoing.** Tax/receipt precision fixes; split the two god files; wire axe into the E2E suite; add CODEOWNERS for money/migration paths.

Nothing here blocks continued development. Items 1 and 2 are the genuine gates between "works" and "safe to carry public payment + PII traffic."

_Full per-finding evidence retained in the four audit transcripts for this session._
