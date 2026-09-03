# Spinr — Engineering Director Teardown (read-only review, 2026-09-01)

Scope: whole repository as of branch `claude/rideshare-code-review-rvm37m`
(backend, rider-app, driver-app, admin-dashboard, shared, CI/CD, infra).
Method: targeted code reading + repo-wide pattern census. No code was changed.
Where a claim is a metric, the command that produced it is reproducible from
the repo root. Where something could not be verified (live Supabase, Stripe,
Fly, Grafana), it is called out as unverified rather than assumed.

Comparison baseline is how Uber/Lyft-class platforms solve the same problem,
scaled to what a Saskatchewan-first, 0%-commission product actually needs.
"Do what Uber does" is not the recommendation; "know which of Uber's
problems you already have" is.

---

## Snapshot (verified metrics)

| Metric | Value | Source |
|---|---|---|
| Backend Python (non-test) | 436 files, ~168k lines | `find backend -name '*.py'` |
| Backend tests | 815 files, 12,534 `def test_` | `grep -rc "def test_"` |
| Coverage gate | 60% (`--cov-fail-under=60`) | `backend/pytest.ini` |
| Migrations | 484 SQL files | `ls backend/migrations` |
| Background loops on every replica | 54 names in `_WATCHDOG_LOOP_NAMES` | `core/lifespan.py` |
| `backend/utils/` modules | 152 | `ls backend/utils/*.py` |
| Dual-import `except ImportError` blocks | 984 | `grep -rc` |
| `except Exception` (non-test) | 1,556; 216 followed by `logger.warning`, 18 by `pass` | `grep -A1` |
| `HTTPException(detail=str(e)/f"…{e}")` | 33 (all in admin routes) | `grep` |
| `print(` in non-test, non-script code | 253 (all in import/backfill/diagnose modules) | `grep` |
| Largest backend files | `routes/admin/drivers.py` 4,310; `routes/admin/rides.py` 3,899; `services/driver_import_service.py` 2,514; `routes/webhooks.py` 2,248 | `wc -l` |
| Largest frontend files | `admin/.../drivers/page.tsx` 3,786; `service-areas/page.tsx` 2,982; `rider-app/app/ride-options.tsx` 2,284; `driver-app/.../index.tsx` 1,732 | `wc -l` |
| Explicit `any` | admin-dashboard/src 451; rider+driver+shared 515 | `grep ": any\|as any"` |
| ESLint warning budget (admin) | `--max-warnings 1751` | `admin-dashboard/package.json` |
| Frontend tests | rider 133, driver 134, admin 88, shared 10 (48 / 40 / 82 screens) | `find -name '*.test.*'` |
| Maestro mobile e2e | 3 flows, `workflow_dispatch` only | `.github/workflows/maestro-e2e.yml` |
| Backend e2e | 7 `test_e2e_*.py`; 14 tests in `test_ride_state_machine.py` | `ls backend/tests` |
| ACTION_ITEMS backlog | 103 open / 161 closed | `grep "\[ \]" ACTION_ITEMS.md` |

Status of the 16 "critical" findings in the earlier `SPINR_CODE_REVIEW.md`:
the ones I re-read are fixed. Driver-cancel now checks ownership
(`routes/drivers/ride_cancel.py:76`), the circuit-breaker probe has a
`release_probe()` (`repositories/_base.py:130`), admin wallet credit/debit go
through `wallet_apply_delta` (`routes/admin/wallet.py:168,272`), and standard
payouts reserve a `payouts` row before `Transfer.create` with an idempotency
key and a reversal path (`routes/drivers/payouts.py:954-1015`). That review
should be marked as remediated rather than left at the repo root as if open.

---

## 🚨 Critical Issues & Security Flaws

Nothing I read is an unauthenticated money-or-data exploit. The criticals are
structural: things that will cause an outage or a regulatory event under
load or on a bad day, not on a normal one.

### C1. Migrations are not part of the deploy pipeline
- **Evidence:** `.github/workflows/deploy-fly.yml` has no migration step;
  `backend/fly.toml` has no `release_command`; `apply-supabase-schema.yml` is
  `workflow_dispatch` only. `CLAUDE.md` confirms migrations are applied by a
  human running `run_migrations.py` with `DATABASE_URL`.
- **Why it matters:** code that expects a column ships before the column exists,
  or the column lands and the code is rolled back. 484 migrations with ~60
  duplicated numeric prefixes (per `CLAUDE.md`) and a nightly duplicate-checker
  is a symptom that schema and code are already drifting. Uber/Lyft treat
  schema change as a gated, automated, pre-deploy step with an expand/contract
  contract. Here it is a runbook.
- **Fix:** add a Fly `release_command` (or a dedicated deploy job) that runs
  `run_migrations.py --status` then applies pending, fails the deploy on error,
  and refuses to run if a `NEVER_APPLY` file is pending. Pair with an
  expand/contract rule in `backend/migrations/CLAUDE.md` so the previous build
  is always compatible with the new schema.

### C2. 54 cron-style loops run in-process on every API replica
- **Evidence:** `core/lifespan.py` `_WATCHDOG_LOOP_NAMES` has 54 entries;
  `try_acquire_leader_lock` in `utils/redis_client.py:433` is best-effort and,
  per `CLAUDE.md`, "fails open on Redis errors". `deploy-fly.yml` scales the
  app to 8 machines (2 warm + 6 suspended) and `UVICORN_WORKERS=2`.
- **Why it matters:** a Redis blip means every replica × every worker believes it
  is the leader. Loops that touch money (`auto_payout`, `payment_retry`,
  `corporate_autotopup`, `orphaned_hold_reconciler`) depend on idempotency keys
  and DB claims to stay safe, which they mostly do, but the API tier's memory
  and event loop are shared with batch work. A slow reconciliation sweep
  competes with a dispatch request for the same 1 GB / shared-CPU machine.
- **Fix:** move the loops to a separate worker process group (same image,
  different entrypoint, `fly.toml` `[processes]` with `web` and `worker`),
  then to a real scheduler with leases (arq or Celery beat with Redis, or
  Temporal if you want durable workflows for payouts and settlement). This is
  a deploy-topology change, not a rewrite: the loop bodies stay.

### C3. DB access is PostgREST-over-HTTP through a 64-thread pool
- **Evidence:** `repositories/_base.py:162` `ThreadPoolExecutor(max_workers=64)`;
  every `db_supabase.*` call is a sync `supabase-py` HTTP round trip run via
  `run_in_executor`. No SQL transactions exist except inside Postgres functions
  (`wallet_apply_delta`, `match_and_claim_driver`).
- **Why it matters:** each request that does 4 reads and 2 writes costs 6 HTTP
  round trips and 6 thread handoffs. The dispatch path (`routes/rides/matching.py`)
  reads drivers, ride, settings, service area, presence, then writes offers.
  Under 2 workers × 64 threads the pool saturates before CPU does, and the
  `spinr_db_thread_pool_*` gauges exist precisely because this has already
  hurt. Multi-row invariants (ride status + driver availability + insurance
  period row) cannot be atomic without an RPC per invariant, which is why the
  repo has accumulated many RPCs and reconciler loops to repair partial writes.
- **Fix:** introduce `asyncpg` (or SQLAlchemy 2 async) against the Supabase
  pooler for the hot paths only: dispatch claim, ride state transitions,
  location marker write, settlement. Keep supabase-py for admin CRUD. Wrap each
  state transition in one transaction that writes `rides`, `drivers`, and
  `driver_insurance_periods` together. This is the single highest-leverage
  performance and correctness change in the codebase.

### C4. Driver search is a bounding-box scan + Python haversine, not a spatial query
- **Evidence:** `routes/rides/matching.py:313-337` deliberately bypasses the
  `find_nearby_drivers` PostGIS RPC because `update_driver_location` never
  populates the `location` column; a trigger-maintained `location_geog` with a
  GiST index has existed since migration 170 but is unused by dispatch.
  Candidates are capped at 500 rows then ranked in Python, with an optional
  Distance Matrix call for the top 15.
- **Why it matters:** fine at 50 online drivers. At 5,000 the bounding box
  returns thousands of rows per dispatch, serialized over PostgREST, ranked on
  the event loop. Uber/Lyft use H3/S2 cells with in-memory driver indexes for
  this exact reason. Spinr does not need that yet, but it has a spatial index
  it already pays to maintain and does not query.
- **Fix:** switch the candidate query to an RPC over `location_geog`
  (`ST_DWithin` + `ORDER BY <->` KNN), keep the Python ranking for ETA and
  fairness. Delete the dead `find_nearby_drivers` RPC or make it the one that
  is used.

### C5. Feature-flagging is settings-table reads, not a flag system
- **Evidence:** flags are keys in `app_settings` (e.g.
  `ledger_atomic_settle_enabled` read in `services/payment_service.py:1491`);
  read failure "assumes off" with a warning. No targeting, no percentage
  rollout, no kill-switch audit trail; `services/pre_launch_flag_service.py` is
  an unrelated data-migration marker, not a flag service.
- **Why it matters:** `CLAUDE.md` mandates dark launches for anything user-visible,
  but the mechanism cannot do a 5% canary or a per-company rollout, and a DB
  hiccup silently flips money-path behaviour to the legacy branch.
- **Fix:** adopt a real flag SDK (Unleash self-hosted, GrowthBook, or Flagsmith)
  with server + RN + Next clients, cached locally with a last-known-good
  default per flag. Migrate the existing `app_settings` booleans behind it.

### C6. Admin `str(e)` leaks in 33 endpoints
- **Evidence:** `grep 'detail=str(e)'` → 33 hits, all in `routes/admin/*`
  (`driver_import.py:88,111`, `drivers.py:2602,3959`, `export_approvals.py:55,82`,
  `legacy_sin_dob_backfill.py:99`, `data_transfer_import.py:72,98` …).
  The global handler (`utils/error_handling.py:_should_sanitize_5xx_detail`)
  only scrubs 5xx; these are 4xx and pass through verbatim.
- **Why it matters:** admin is authenticated, so this is not a public leak, but
  import/backfill services raise with file paths, row contents, and SIN/DOB
  column names. That text lands in browser history, Vercel logs, and Sentry
  breadcrumbs. PIPEDA does not distinguish "only admins saw it".
- **Fix:** raise `SpinrException` subclasses with a user-safe `message` and put
  the raw text in `details` behind the existing scrubber, or extend
  `_should_sanitize_*` to 4xx in the admin router.

---

## 🛡️ Error Handling & Telemetry

### What is good (and better than most startups)
- A real error envelope: `ErrorCode` enum, `SpinrException` hierarchy, global
  handlers for `SpinrException`/`RequestValidationError`/`HTTPException`/
  `Exception` (`utils/error_handling.py:886-891`), `request_id` bound to
  loguru context and returned in the body so a user-reported error can be
  joined to logs (`error_handling.py:27-41`).
- 5xx detail is sanitized unless it is an `ERR_*` sentinel. Riders never see a
  stack trace from the backend.
- Sentry with PIPEDA `before_send` scrubber and `surface`/`domain` tags
  (`server.py:527-551`, `utils/sentry_scrub.py`), sample rate 0.1.
- Rate limiter keys on `CF-Connecting-IP`, not spoofable `X-Forwarded-For`
  (`utils/rate_limiter.py:217`). The two `verify_signature=False` decodes
  (`core/middleware.py:447`, `rate_limiter.py:124`) are for log correlation
  and bucket keying only; they are not auth decisions.
- Mobile: `shared/api/client.ts` has a single in-flight refresh with a
  subscriber queue (lines 125-141), an absolute deadline header, and
  `errorPresentation.ts` maps `error.details.field` to field-level toasts.
  Tokens live in `SecureStore` (`shared/store/authStore.ts:72-83`).
  `useRiderSocket.ts` reconnects with 1/2/5/10/30 s backoff and refetches ride
  state on reconnect (line 163).
- Metrics are actually scraped: `metrics-agent/` runs Grafana Alloy on Fly and
  remote-writes to Grafana Cloud. Loop staleness posts to a Slack webhook
  (`utils/loop_alert.py`). `docs/runbooks/on-call.md` names PagerDuty.

### What is not
1. **Swallow census.** 216 `except Exception → logger.warning` and 18 `→ pass`
   in non-test code. Most are defensible (best-effort push, offer-card URL).
   The ones that are not: `payment_service.py:1491` (flag read failure flips a
   money path), `matching.py:1365` (Redis skip-key failure means a timed-out
   driver is re-offered the same ride), `matching.py:1260` (driver not told the
   offer expired, so the driver app shows a dead offer). Rule from `CLAUDE.md`
   is `logger.error` + surface; enforce it with a Ruff custom rule or a
   pre-commit grep on `except Exception` + `warning` inside `routes/{rides,
   payments,wallet,webhooks}` and `services/{payment,dispatch}*`.
2. **`print()` in 253 places** across import/backfill/diagnose services that
   are importable from the production package (`services/driver_import_service.py`,
   `insurance_period_gps_correction.py`, `diagnose_nearby_drivers.py`,
   `list_users.py`). They bypass log context and Sentry. Move them under
   `backend/scripts/` with a `__main__` guard, or replace with `logger`.
3. **Two log formats.** loguru context plus stdlib `logging.getLogger(__name__)`
   per module. Pick one sink (loguru intercept handler is already the
   pattern), emit JSON in production, and ship to a log store with retention
   (Grafana Loki fits the existing Alloy agent).
4. **No distributed tracing.** `X-Trace-ID` is emitted (`core/middleware.py:478`)
   but nothing propagates it into Supabase, Stripe, Redis, or WS fan-out.
   `opentelemetry` is absent from `requirements.in`. Without spans the "why was
   this dispatch 2.4 s" question is answered by reading logs. Add
   `opentelemetry-instrumentation-fastapi` + httpx + redis, export OTLP to
   Grafana Tempo via the existing Alloy agent.
5. **Alerting is declarative, not live.** `monitoring/synthetic-checks.yaml`
   says in its own header that no vendor consumes it. Loop alerts go to Slack,
   not PagerDuty. SLOs in `CLAUDE.md` (dispatch P95 < 2 s, settlement < 1 s)
   have no burn-rate alert. Wire Grafana Cloud alert rules on the
   `spinr_dispatch_offer_to_accept_duration_ms` histogram and route SEV-1 to
   PagerDuty as the runbook already promises.
6. **Mobile telemetry duplication.** Rider app carries Sentry RN, Firebase
   Crashlytics, LogRocket, `expo-insights`, and `expo-observe`, plus the
   Firebase *web* SDK (`"firebase": "^12.18.0"`) with zero imports. Five
   telemetry SDKs in a 0%-commission app is cost, bundle size, and five
   places PII scrubbing has to be right. Keep Sentry + Crashlytics, drop the
   rest unless someone reads them weekly.
7. **Admin dashboard** shows `errorData.detail` strings directly
   (`shared/api/cachedClient.ts:103,129,156`), which is where C6 surfaces to
   a human.

---

## 🐢 Performance Bottlenecks & Optimizations

Ordered by SLA impact.

| # | Path (SLA) | Bottleneck | Evidence | Fix |
|---|---|---|---|---|
| P1 | Dispatch offer → notification (< 2 s) | Sequential PostgREST reads (ride, settings, area, drivers ≤ 500 rows, presence per driver) then Python ranking, then optional Distance Matrix (bounded by the offer clock) | `matching.py:300-460, 792-816` | C3 + C4; batch presence via one `MGET`; cache `app_settings`/`service_areas` in Redis with a 30 s TTL |
| P2 | Driver location write (< 150 ms) | Reasonable: `should_write_marker` coalesces marker updates (`routes/drivers/location.py:67`) and breadcrumbs are batched. Remaining cost is the PostgREST round trip itself | `location.py`, `utils/location_write_gate.py` | Keep; move the marker write to asyncpg when C3 lands. Consider writing the live marker only to Redis (GEOADD) and flushing to Postgres every N s, which is the Uber pattern |
| P3 | Fare estimate (< 300 ms, accepted 3.5 s exception) | Google Directions awaited inline by design | `routes/rides/estimates.py` | `deploy/osrm` exists but backend only references OSRM in `ride_repo.py` for analysis. Put self-hosted OSRM/Valhalla in front for the estimate, fall back to Google for the quote-locked confirm. Halves cost and latency |
| P4 | WS fan-out (< 100 ms) | In-process dict + Redis pub/sub is fine; `broadcast_driver_location_to_admins` iterates all admin connections per driver ping | `socket_manager.py:460, 545` | Throttle admin map updates to 1 Hz per driver server-side; use a single `admin_map` channel with batched payloads |
| P5 | Admin dashboards | Pages of 2,000–3,800 lines doing client-side filtering; 82 pages, 14 Next API routes proxying to backend | `admin-dashboard/src/app/dashboard/drivers/page.tsx` | Server-side pagination on every list endpoint (backend `_DEFAULT_ROW_LIMIT=1000` is a cap, not pagination); React Query with cursor pagination; split pages into feature modules |
| P6 | Thread pool starvation | 64 threads × 2 workers; every DB call blocks a thread for the full HTTP RTT to Supabase | `_base.py:162` | C3. Until then, raise `UVICORN_WORKERS` only with more memory; the fly.toml comment already says CPU is the constraint |
| P7 | Stripe calls | Correctly off-loaded via `asyncio.to_thread` with idempotency keys on payouts/top-ups; `Subscription.create/modify/delete` in `corporate_subscription_service.py:125,212` have none | — | Add `idempotency_key` derived from company + plan + period |
| P8 | Redis fallback | With `REDIS_URL` unset, rate limit, OTP lockout, leader locks and presence become per-process dicts and silently pass | `utils/redis_client.py:4-8` | Fail startup in `ENV=production` when `REDIS_URL` is empty (config already fails fast on weak secrets; extend it) |

Uber/Lyft comparison: they separate the "hot" state (driver positions, open
offers) into an in-memory geo store and treat Postgres as the system of
record written asynchronously. Spinr's equivalent is Redis presence +
`drivers` row; the gap is that dispatch still reads the row, not Redis. Close
that and the dispatch SLA becomes a Redis latency, not a PostgREST one.

---

## 💡 Tech Stack & Architecture Recommendations

| Layer | Current | Verdict | Recommendation | Why |
|---|---|---|---|---|
| API framework | FastAPI 0.141 / Pydantic 2 / uvicorn 2 workers | Keep | — | Right choice; the problems are around it, not in it |
| DB driver | supabase-py (PostgREST) via thread pool | **Replace on hot paths** | asyncpg or SQLAlchemy 2 async through the Supabase pooler; keep supabase-py for admin CRUD | Transactions, batching, no thread pool, 2–5× fewer round trips |
| Geo | Python haversine over bounding box | **Replace** | PostGIS KNN RPC now; Redis `GEOSEARCH` for live positions next; H3 only if multi-city | Index already exists (migration 170) |
| Background work | 54 asyncio loops in the API process | **Replace** | Fly `[processes]` worker group → arq/Celery with Redis leases → Temporal for payouts/settlement sagas | Isolation, durable retries, one leader by construction |
| Scheduler/leader | Redis `SET NX` fail-open | Replace with above | — | Fail-open on money loops is the wrong default |
| Realtime | In-process registry + Redis pub/sub | Keep, harden | Add per-connection outbound queue with drop-oldest for location, sequence numbers on ride events so clients detect gaps | Missed-event resync today is "refetch on reconnect", not gap detection |
| Routing/ETA | Google Directions + Distance Matrix inline | **Add** | Self-hosted OSRM/Valhalla (deploy/osrm exists) for estimates and ranking; Google for the locked quote | Cost and the 3.5 s estimate exception |
| Cache | Redis, mostly for presence and locks | Add | Cache `app_settings`, `vehicle_types`, `service_areas`, fare config with short TTL + pub/sub invalidation | These are read per request today |
| Feature flags | `app_settings` booleans | **Add** | Unleash / GrowthBook / Flagsmith with RN + Next SDKs | C5 |
| Tracing | None | **Add** | OpenTelemetry → Grafana Tempo via existing Alloy | Only way to defend the SLA table |
| Logs | loguru + stdlib, stdout | Add | JSON + Loki via Alloy, 30-day retention, PII lint on log lines | Currently Fly log tail only (unverified) |
| Contract | Hand-written TS types in `shared/types` | **Add** | Generate `shared/build-types` from FastAPI's OpenAPI in CI; fail PR on drift | 451 + 515 `any` is the symptom |
| Payments | Stripe + Connect, idempotent, pre-auth, ledger service | Keep | Enforce idempotency key lint on every `stripe.*.create/modify`; add a DLQ table for failed webhook handlers instead of `unclaim_stripe_event` retry-on-next-delivery | Already strong |
| Mobile | Expo 57 / RN 0.86 / React 19, EAS, OTA | Keep | Remove Firebase web SDK, LogRocket, expo-insights/observe; keep Sentry + Crashlytics; add crash-free-rate gate before OTA promote | Bundle and PII surface |
| Admin | Next 16 / React 19 / Tailwind / zustand / zod / recharts | Keep | Split mega-pages; adopt React Query + server pagination; raise lint bar to `--max-warnings 0` on changed files via `eslint --max-warnings` + `lint-staged` | 1,751 budget is a bankruptcy declaration |
| Hosting | Fly (yyz) primary, Railway standby via DNS | Keep, fix | Restore Railway deploy (ACTION_ITEMS C5) or drop the standby claim; add a health-based Cloudflare load balancer if you keep two | A stale standby is worse than none |
| CI | GH Actions: pytest + Postgres service, bandit, Trivy, ZAP weekly, migration-check, Dependabot auto-merge | Keep, extend | Add migrations to deploy (C1), OpenAPI drift check, mobile typecheck, Maestro on PR label (currently manual), coverage ratchet to 70 per pytest.ini's own plan | Gates exist; the missing ones are ordering and contract |
| Secrets | Stripe/Twilio/Maps keys in `app_settings` table | Reconsider | Keep rotation-without-deploy, but move to Fly secrets + a cached `/internal/reload` endpoint, or Doppler/Infisical | A DB read for the Stripe key on every settlement is both latency and blast radius |
| AI assistant | Anthropic + OpenAI + Gemini SDKs all installed | Trim | One provider SDK; keep the PII-scrub gate | Three SDKs in `requirements.in` is attack surface and audit burden |

---

## 🛠️ Maintainability & Code Smells

1. **God files.** Seven backend files over 2,000 lines; `routes/admin/drivers.py`
   is 4,310. Frontend: four files over 2,200 lines, `drivers/page.tsx` at 3,786.
   These are the files every incident touches. Split by sub-resource
   (documents, payouts, compliance, status) with the existing router pattern.
2. **`utils/` is a junk drawer.** 152 modules mixing cross-cutting helpers
   (`crypto`, `metrics`, `redis_client`) with domain jobs (`t4a_annual_job`,
   `stale_p3_closer`, `route_reconstruction_projection`). Create
   `backend/jobs/` for loop bodies, `backend/domain/<area>/` for domain
   helpers, keep `utils/` for the 20 things that are actually generic.
3. **Duplicated helpers.** 32 module-local definitions of `_d`/`_round`/`_q`/
   `_money`; 57 copies of `raise HTTPException(404, "Ride not found")`. One
   `utils/money.py` exists; make it the only one and add a `get_ride_or_404`
   dependency.
4. **Dual-import pattern, 984 times.** It exists because the package is run
   both as `python -m backend.server` and as top-level. Installing `backend`
   as a package (`pip install -e .` with a `pyproject.toml`) removes all 984
   blocks and the `# type: ignore` noise. `CLAUDE.md` says do not simplify;
   the right move is to fix the packaging so the rule can be retired.
5. **Legacy migration code in the production import graph.** 15
   `services/*import*|*backfill*|legacy_*|*correction*|*cleanup*` modules,
   `diagnose_*.py`, `list_users.py`, `seed_vehicle_types.py` at package root,
   253 `print()`s. Move to `backend/scripts/legacy/` and out of the mounted
   routers once the Oct 31 legacy decommission lands.
6. **Repo root clutter.** Three `.docx`, one `.csv`, one ad-hoc `.sql`, a
   deprecated `frontend/` tree, `plans/`, `reports/`, `test_reports/`
   committed. Move review artifacts to `docs/audit/`, delete `frontend/`.
7. **Docs drift.** `ARCHITECTURE.md` says Expo SDK 54 and Railway hosting;
   `package.json` says Expo 57 and `CLAUDE.md` says Fly primary. `CLAUDE.md`
   itself is ~600 lines of accumulated incident notes; split the rules from
   the history.
8. **Type discipline.** `strict: true` everywhere, yet ~966 explicit `any`.
   Generated API types (contract row above) fix most of it mechanically.

---

## 🧪 Testing & QA (Missing Edge Cases)

What exists is above average: 12.5k backend tests, real Postgres service in
CI, an RLS-role test tier, mock Supabase fixtures, Playwright + axe in admin,
Jest on both apps, bandit/Trivy/ZAP gates. The gaps are in *what* is tested,
not *whether*.

Missing or unverified edge cases (state machine file has 14 tests; the
matrix below needs ~40):

| Area | Case | Why it matters |
|---|---|---|
| Dispatch | Two drivers accept the same offer within the same 100 ms; assert exactly one 200 and one 409 + `ride_taken` WS | The optimistic filter is the only guard |
| Dispatch | Offer times out at T, driver accepts at T+50 ms | Reaper vs accept race |
| Dispatch | Redis down during dispatch → skip-key write fails (`matching.py:1365`) → same driver re-offered | Currently a warning |
| Lifecycle | Rider cancels while driver is `driver_arrived` and the no-show fee clock has started | Fee + insurance period 2→1 + hold release must all land |
| Lifecycle | Driver app killed mid-`in_progress`; stale-P3 closer vs driver resuming | Two writers on one ride |
| Scheduled | Scheduled ride across DST change (Saskatchewan has no DST, but riders schedule from other zones) | `scheduled_rides` boundary |
| Money | Duplicate Stripe webhook after `unclaim_stripe_event` on handler failure | Replay safety of every handler, not just claim |
| Money | `payment_intent.succeeded` arrives before the ride row is `completed` | Ordering |
| Money | Tip added after driver payout already transferred | Second transfer with its own key |
| Money | Corporate allowance exhausted between estimate and settlement | Fallback to master wallet vs rider card |
| Money | Surge changes between estimate and confirm with an expired estimate token | Token TTL path |
| Money | Partial refund then dispute `charge.dispute.created` on the same charge | Ledger double-entry |
| Location | GPS jump > 200 km/h between pings; timestamp from client in the future | `gps_filtering` / `location_integrity` coverage |
| Auth | Refresh-token reuse after rotation (theft detection) | Rotation exists; reuse detection assertion needed |
| Auth | WS connection outlives token expiry by 20 min | Server-side revalidation |
| Input | Emoji / RTL / 10 kB address strings through PostgREST `$regex` | Escaping layer |
| Mobile | App resumes after 30 min in background mid-ride; WS resync vs push vs REST all disagree | Three sources of truth |

Process gaps:
- Coverage gate is 60%; pytest.ini's own plan says 70 by July. Ratchet it.
- Maestro flows never run automatically (workflow_dispatch). Run on a
  `run-maestro` label as `label-run-maestro.yml` suggests, and nightly.
- Load test (`loadtest/locustfile.py`, RiderBot/DriverBot) is not in any
  workflow and has no stored baseline to compare against.
- No contract test between backend OpenAPI and `shared/types`.
- Hollow-test risk: the mocked Supabase fixture returns whatever the test
  seeds, so many money tests assert the code's own arithmetic. The RLS tier
  and `test_payout_toctou.py` are the model; extend the real-Postgres tier to
  settlement and `wallet_apply_delta`.

---

## 📈 Manager's Verdict

**Overall health: B-.** This is a serious codebase, not a prototype. The
conventions in `CLAUDE.md` are unusually specific and mostly enforced;
the earlier review's criticals were actually fixed; money paths are
idempotent and Decimal; error envelopes and PII scrubbing exist; CI has
real gates. Most ride-share startups at this stage have none of that.

The risk is not a bug. It is topology. One process runs the API, the
WebSocket registry, and 54 batch loops, talks to Postgres over HTTP through
a thread pool, scans drivers in Python, and applies schema by hand. Each of
those was a reasonable early choice; together they cap the platform at
roughly one city and make every incident a "which of the 54 loops did it"
investigation.

Grades:

| Dimension | Grade | One line |
|---|---|---|
| Correctness | B+ | State machine and money guards are real; races are guarded by filters, not transactions |
| Security | B+ | Good JWT/OTP/rate-limit posture; admin `str(e)` leaks and DB-stored API keys are the remaining smell |
| Error handling / telemetry | B | Envelope and scrubber are excellent; 216 warn-and-continue and no tracing are not |
| Performance | C+ | Every SLA is one PostgREST round-trip storm away from breach |
| Architecture | C+ | Monolith is fine; monolith + in-process cron + HTTP DB is not |
| Maintainability | C | 4k-line files, 152 utils, 984 dual imports, 1,751 lint warnings |
| Testing | B | Volume is high; edge-case matrix and contract tests are thin |
| Process | B+ | Gates, runbooks, ADRs, change-impact log; migrations still manual |

**90-day plan (ordered, each step independently shippable):**

1. **Weeks 1–2, zero-risk hygiene:** move migrations into deploy (C1); fail
   startup without `REDIS_URL` in production (P8); scrub admin `str(e)` (C6);
   delete `frontend/`, root `.docx/.csv/.sql`, unused Firebase web SDK;
   mark `SPINR_CODE_REVIEW.md` remediated and move it to `docs/audit/`.
2. **Weeks 2–4, isolation:** split `web` and `worker` Fly process groups; the
   54 loops run only in `worker`; leader lock fails closed for money loops.
   No loop code changes.
3. **Weeks 3–6, hot-path data layer:** asyncpg for `claim`, ride transitions,
   location marker, settlement; PostGIS KNN RPC for candidates (C3, C4).
   Measure with the existing histograms before/after.
4. **Weeks 4–8, observability:** OpenTelemetry → Tempo; JSON logs → Loki;
   Grafana alert rules on the SLA histograms → PagerDuty; retire LogRocket
   and expo-observe.
5. **Weeks 6–10, contracts and flags:** OpenAPI-generated TS types with a
   drift gate; a real flag service replacing `app_settings` booleans.
6. **Weeks 8–12, decomposition:** split the seven 2k+ backend files and four
   2k+ pages; package `backend` so the dual-import rule can be deleted;
   ratchet coverage to 70 and lint budget toward zero on touched files.

What I did **not** verify: live Supabase indexes and RLS state, Fly/Grafana
alert configuration, Stripe dashboard settings, actual P95 numbers, mobile
bundle size, and whether Railway standby currently builds. Those need
production access, not code reading.
