# Spinr — Engineering Director Teardown, Round 2 (read-only, 2026-09-03)

**Scope:** whole repository at `bf5d0ac` (branch `claude/rideshare-code-review-xewaez`): `backend/`, `rider-app/`, `driver-app/`, `admin-dashboard/`, `shared/`, CI/CD, deploy config.
**Method:** repo-wide census (every number below has its command), ten domain reviews (security, dispatch/state machine, money, performance, realtime/loops, error handling/telemetry, testing, CI/CD, mobile, admin), each re-verified against HEAD with `file:line` evidence, plus a comparison against how Uber/Lyft-class platforms solve the same problem, scaled to a Saskatchewan-first, 0%-commission operation.
**Relationship to prior work:** this is a follow-up to `docs/audit/2026-09-01-engineering-director-teardown.md` and `2026-09-01-path-to-a-grade.md`. Findings there that are still true are cited, not repeated. New findings, corrections to the prior doc, and everything that changed in the 60 commits since (C50 direct pool, H3/outbox dark launch, CI real-Postgres tiers) are called out explicitly.
**No code was changed.** Where something could not be verified without production access (live Supabase, Fly, Grafana, Stripe, a device), it is listed as unverified rather than assumed.

---

## Snapshot (verified 2026-09-03)

| Metric | Value | Command |
|---|---|---|
| Backend Python, non-test | 450 files / ~173k lines | `find backend -name '*.py' -not -path '*/tests/*'` |
| Backend tests | 825 files / 12,839 `def test_` | `grep -rc 'def test_' backend/tests` |
| Global coverage gate | 60% (`pytest.ini:15`); scoped per-package floors **are** enforced in `ci-guardrails.yml` (money ≥90, corporate ≥80, admin ≥70) — CLAUDE.md's "not yet enforced" line is stale | `grep cov-fail-under backend/pytest.ini` |
| Migrations | 492 files, 68 numeric prefixes shared by 2+ files | `ls backend/migrations \| grep -oE '^[0-9]+' \| sort \| uniq -d` |
| Background loops on every API process | 40 in `_WATCHDOG_LOOP_NAMES` (+1 cataloged but never spawned) | `core/lifespan.py:720-767`, `core/background_loop_registry.py:31-75` |
| Replica × worker | 8 Fly machines × `UVICORN_WORKERS=2` = up to 16 copies of each loop | `backend/fly.toml`, `deploy-fly.yml:117` |
| `except Exception` (non-test) | 1,621; 217–222 followed by `logger.warning`; 17 by bare `pass` | `grep -rn -A1 'except Exception' backend --include=*.py \| grep -v /tests/` |
| `HTTPException(detail=str(e) / f"…{e}")` | 31 (19 + 12), 29 admin, 2 driver-facing | `grep -rn 'detail=str(e)' backend` |
| `print()` in importable non-script modules | 253 (all legacy import/backfill/diagnose services) | `grep -rn '^\s*print(' backend --include=*.py \| grep -v /tests/ \| grep -v /scripts/` |
| Dual-import `except ImportError` blocks | 1,012 | `grep -rc 'except ImportError' backend` |
| `backend/utils/` modules | 157 | `ls backend/utils/*.py` |
| Route files importing `db_supabase` directly | 100 of 128 | `grep -rl db_supabase backend/routes` |
| Largest backend files | `routes/admin/drivers.py` 4,334 · `routes/admin/rides.py` 4,030 · `services/driver_import_service.py` 2,514 · `routes/webhooks.py` 2,248 · `services/payment_service.py` 2,123 · `routes/auth.py` 2,062 | `wc -l` |
| Frontend TS/TSX lines | rider 63,305 · driver 67,417 · admin 91,250 | `find … \| xargs wc -l` |
| Largest frontend files | `admin …/drivers/page.tsx` 3,786 · `admin …/service-areas/page.tsx` 2,982 · `rider-app/app/ride-options.tsx` 2,284 · `driver-app/hooks/useDriverDashboard.ts` 1,910 | `wc -l` |
| Explicit `any` | admin-dashboard/src 451 · rider-app/app 288 · driver-app/app 197 | `grep -rE ': any\b\|as any\b'` |
| ESLint budget | admin `--max-warnings 1751`; rider/driver `expo lint` with no budget; `no-explicit-any` off in admin | `package.json` |
| `console.*` in mobile app code | rider 78 · driver 120 (no babel strip, no `__DEV__` gate) | `grep -rn 'console\.' <app>/{app,components,hooks,utils,store}` |
| Frontend tests | rider 133 · driver 134 · admin 88 (59 vitest + 29 Playwright) · shared 10 | `find -name '*.test.*'` |
| Admin vitest coverage gate | branches 11 / functions 10 / lines 19 / statements 18 | `admin-dashboard/vitest.config.ts:31-36` |
| ACTION_ITEMS backlog | 64 open / 160 closed | `grep -c '^\s*- \[ \]' ACTION_ITEMS.md` |
| Changed since the 09-01 teardown | 60 commits, 178 files, +25,075 / −2,880 | `git diff --stat <09-01>..HEAD` |

### What changed since 2026-09-01 (and what it does and does not fix)

- **C50 direct-pool dispatch claim landed** (`repositories/dispatch_pool.py`, psycopg v3 `AsyncConnectionPool`, migration 403 `dispatch_claim_batch` with `FOR UPDATE SKIP LOCKED`, transaction-local `statement_timeout`, `prepare_threshold=None` for Supavisor transaction mode). It is wired in `routes/rides/matching.py:867-1100` behind `dispatch_direct_pool_enabled` (default **off**, migration 401). This is the single most important structural improvement since the last review: claim + `ride_offers` insert + Period-2 insurance write become **one transaction**, which also fixes C54 (unreleased claims on mid-loop exception) — but only on the flag-on path. Real-Postgres tests for it now run in CI (`ci.yml:172`).
- **H3 live-location index + PostGIS candidate RPC landed dark** (`dispatch_geo_provider=legacy`), then needed two follow-up fixes just to import (C51 missing `h3` pin, C52 six missing Redis helpers). C53 lists four more unwired pieces. The `h3_index_reconciler` loop is cataloged but never spawned (see Critical #2).
- **Transactional outbox + `worker.py` landed dark** (`outbox_receipts_enabled=false`). The worker process is not deployed and the API never consults the process role (see Critical #2).
- **Real-Postgres CI tiers**: `tests/rls` and `tests/direct_pool` now run in `ci.yml` against the `postgres:15` service. Good. The two wallet RPCs are still not in either tier.
- **Admin N+1 payout paths fixed** (`d19394a`), per-phase dispatch timing and `run_sync` queue-wait histograms added (`fe19931`, `49105ac`).
- **Unchanged and still open:** migrations outside deploy (C1), no staging environment, admin `str(e)` leaks (C6), loops in-process (C2), `app_settings`-as-flags (C5), no tracing, `frontend/` and root `.docx/.csv/.sql` clutter, `ARCHITECTURE.md` still says Expo SDK 54 / Railway.

---

## 🚨 Critical Issues & Security Flaws

Ranked by blast radius on a live-tested product. None is an unauthenticated exploit; several are "one bad afternoon away" from a rider-visible incident or a regulatory record.

| # | Severity | Finding | Evidence | Status |
|---|---|---|---|---|
| 1 | **Blocker** | Admin force-cancel accepts an `in_progress` ride, does a read-then-write, and never closes the insurance period | `routes/admin/rides.py:598-619, 646-655` | Verified, no test for the `in_progress` case |
| 2 | **Blocker** | The worker-tier split is inert code: `worker.py` and a loop registry exist, but `core/lifespan.py` never reads `SPINR_PROCESS_ROLE`, `fly.toml` has `processes = ["app"]` only, and `deploy-fly.yml` scales one group. `h3_index_reconciler` is cataloged and never spawned | `core/background_loop_registry.py:59,91`, `backend/fly.toml:56`, `deploy-fly.yml:117` | Verified |
| 3 | **Blocker** | Schema and code still deploy independently; no staging gate; Fly (the live primary) has no automated rollback while the idle Railway standby does | `deploy-fly.yml` (no `run_migrations`), `fly.toml` (no `release_command`), `deploy-backend-staging.yml:1-32` ("WILL FAIL UNTIL A HUMAN COMPLETES MANUAL SETUP"), `deploy-backend.yml:105-111` (`railway rollback`) | Verified; prior incident C44 |
| 4 | High | Dispatch claim races on the live (flag-off) path: C54 open (mid-loop exception leaves earlier claims held ~150 s); single-offer timeout revert `driver_assigned→searching` filters on `id` only (TOCTOU), reachable from admin direct-assign | `routes/rides/matching.py:1030-1049`, `:1431-1442`, `routes/admin/rides.py:1150-1193` | Verified |
| 5 | High | Money-path flags fail silently: corporate kill-switch read failure **fails open** (billing proceeds); `ledger_atomic_settle_enabled` read failure silently downgrades to the legacy settle path | `services/payment_service.py:941-942`, `:1491-1493` | Verified |
| 6 | High | Admin dashboard edge middleware only base64-decodes the JWT and checks `exp`; no signature verification. Backend still 403s data, but the shell, nav, and module list render for a forged cookie | `admin-dashboard/src/middleware.ts:133-140` | Verified |
| 7 | High | Bulk ride export (highest-volume PII egress) is module-gated but writes no audit row, unlike `reveal-sin` which audits before decrypt | `backend/routes/admin/rides.py:281-312` vs `routes/admin/drivers.py:3858` | Verified |
| 8 | High (PIPEDA) | LogRocket session replay initialised with no privacy config on both apps; only 4 screens pause capture. Ride maps, addresses, and phone numbers are captured on iOS | `rider-app/app/_layout.tsx:320`, `driver-app/app/_layout.tsx:372`, `shared/hooks/useLogRocketPrivacyScreen.ts:36-42` | Verified (capture on device not observed) |
| 9 | Medium | React Query persister writes every successful query, including the driver's `activeRide` (pickup address, rider contact), to plain AsyncStorage for 24 h; no `shouldDehydrateQuery` | `shared/api/queryClient.ts:76-84`, `driver-app/app/_layout.tsx:592-598` | Verified (shape) |
| 10 | Medium | Redis outage makes the offer-cooldown key write a warning, so a timed-out driver can be re-offered the same ride on the next attempt | `routes/rides/matching.py:1493-1500`, `:1591-1594` | Verified |
| 11 | Medium | Google Maps key ships in the rider bundle and is used for a client-side Directions fallback, so it cannot be app-restricted | `rider-app/app/ride-options.tsx:23, 466-479` | Verified |
| 12 | Medium | Generic `REDIS_URL` has no production fail-fast (`RATE_LIMIT_REDIS_URL` does), so OTP lockout, presence, and leader locks can silently become per-process dicts | `core/middleware.py:695-786` vs `utils/redis_client.py` fallback | Verified |
| 13 | Low | 31 `detail=str(e)` sites; the 5xx sanitizer is deliberately 4xx-passthrough; 2 are driver-facing | `utils/error_handling.py:739`, `routes/drivers/appeals.py:79`, `routes/drivers/tax_exports.py:497` | Verified |
| 14 | Low | Backend uses the service-role key everywhere, so RLS is bypassed by design and route ownership checks are the only gate; 11 of ~127 policies have role-level tests | `supabase_client.py:11,20`, `backend/tests/rls/` | Verified |
| 15 | Low | Two Stripe calls without idempotency keys (`Subscription.modify`, admin-invoice `Customer.create`); a vestigial `/drivers/bank-account` endpoint accepts routing numbers with no step-up auth and never reaches Stripe | `services/corporate_subscription_service.py:212`, `routes/admin/rides.py:1837-1844`, `routes/drivers/payouts.py:705-734` | Verified |
| 16 | Info | Device attestation is a self-reported heuristic, not App Check; no certificate pinning; `settings.ENV` is an untyped string so `Production`/`prod` would fall through to the dev OTP branch | `utils/device_attestation.py:44-90`, `core/config.py:321`, `routes/auth.py:446` | Verified / Plausible |

### Why each of the top eight matters, and how to fix it

**1. Admin cancel of an in-progress trip.** CLAUDE.md's first invariant is "never `cancelled` after trip start." `admin_cancel_ride` only rejects `completed`/`cancelled`, then calls `update_ride` with no status predicate. A single ops click (or the bulk-operations page) strands a rider mid-trip with no settlement, frees the driver while a passenger is aboard, and leaves the Period-2/3 `driver_insurance_periods` row open — a regulatory audit gap, not a UX bug. *Fix:* apply the same allowed-state check `admin_complete_ride` already has (`:757`), make the write a conditional update filtered on the status just read, and call `record_period_transition(driver_id, 1)` on release. Add the missing test. Uber/Lyft ops consoles gate force-cancel to pre-pickup states for exactly this reason.

**2. The worker split that is not one.** The 09-01 review's top structural risk was 54 (now 40) cron loops sharing the API's event loop and memory. The remedy was built (`worker.py`, `WORKER_WAVE1_LOOP_NAMES`, `should_spawn_on_api()`) but nothing calls it from `lifespan.py`, no Fly process group runs it, and the outbox poller therefore never runs. This is worse than "not started": the codebase reads as fixed. Meanwhile every loop still runs 16-wide, and `try_acquire_leader_lock` fails open (`redis_client.py:647`). The loops that move money all rely on atomic DB claims rather than the lock, which is correct, so a Redis blip costs duplicate scans rather than duplicate payouts — but the API still pays the CPU. *Fix:* wire `should_spawn_on_api()` into `_spawn`, add `[processes] app / worker` to `fly.toml` with `scale count app=8 worker=1`, spawn `h3_index_reconciler`, and add a startup assertion that every catalog entry is either spawned here or owned by the other role.

**3. Deploy without schema, without staging, without rollback.** A merge to `main` deploys to Fly within minutes; the migration it depends on is applied by a human later. This already produced C44 (migrations 363–369 unapplied in production after code merged). The staging workflow's own header says it will fail until someone provisions the environment, so DAST (`dast-zap-baseline.yml`) has never run against anything and the load test has only ever run once, by hand. *Fix:* Fly `release_command = "python -m backend.scripts.run_migrations"` that refuses `NEVER_APPLY` files and fails the deploy; expand/contract rule in `backend/migrations/CLAUDE.md`; `flyctl releases rollback` on the existing health-probe failure branch (mirror `deploy-backend.yml:105-111`); provision staging per the workflow's runbook and gate `main` through it. Uber/Lyft treat schema change as its own gated step and never let the code that reads a column ship in the same deploy that creates it.

**4. Claim path races on the path that is actually live.** `dispatch_direct_pool_enabled` is off, so every dispatch still issues one PostgREST `UPDATE` per candidate. An exception on candidate 4 leaves candidates 1–3 `is_available=false` until the orphan-claim reaper (~150 s), directly hurting the ≥85% match-rate KPI. The single-offer timeout handler's revert has no `status`/`driver_id` filter, so a driver who accepts in the same window can be silently bounced back to `searching`. *Fix:* wrap the PostgREST claim loop in `try/except` that releases `claimed_drivers` (the `ride_offers` insert failure branch 40 lines below already does this), add `{"status": "driver_assigned", "driver_id": driver_id}` to the revert filter, and treat flipping the direct-pool flag on as the durable fix once T16 staging data exists.

**5. Kill-switch that fails open.** A `corporate_billing_enabled` read error during a Supabase incident is precisely when the switch is needed, and it currently logs a warning and proceeds. *Fix:* fail closed with a 503 on that specific read (an admin can re-enable), log at `error` with the original exception, and count fallbacks on `ledger_atomic_settle_enabled` so a settings blip degrading settlement safety is visible. Document either choice as an ADR.

**6. Decode-only admin middleware.** `jose`'s `jwtVerify` runs on the Vercel Edge runtime with `JWT_SECRET`; the comment saying it cannot is dated. Alternatively put Cloudflare Access in front of the admin host, which is how Uber/Lyft internal tools terminate auth, and the middleware becomes defence in depth.

**7. Unaudited bulk export.** Add `audit_log(action="rides.export", filters, row_count)` at the top of `admin_export_filtered_rides`, and route exports above N rows through the existing `export_approvals` flow.

**8. Session replay without redaction.** Pass `redactionTags` / `network.requestSanitizer` to `LogRocket.init`, add `useLogRocketPrivacyScreen` to every ride screen, or flip the existing `EXPO_PUBLIC_ENABLE_LOGROCKET` flag to default-off. Neither Uber nor Lyft ships third-party replay in production; Sentry + Crashlytics already cover crash diagnosis here.

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

### What the end user sees — mostly right

- **Backend envelope is sound.** `SpinrException` hierarchy, `ErrorCode` enum, four global handlers, `request_id` in every body, 5xx `detail` sanitized unless it matches an `ERR_*` sentinel (`utils/error_handling.py:606-891`). 422s are `{field, message, type}` with no internal jargon. Only two `detail` strings contain infrastructure words ("Vault key or RPC", "SUPABASE_URL not configured"), both admin-only.
- **Mobile error UX is deliberate.** `shared/api/client.ts` normalises everything into `SpinrApiError{code, messageKey, actionHint, requestId}`; `errorPresentation.ts` maps codes to copy; an ESLint `no-restricted-syntax` rule forbids `Alert.alert(error.message)` in both apps (0 violations); `OfflineBanner` on NetInfo in both layouts; 426 → forced upgrade; 429 → `RateLimitError` with Retry-After.
- **Dependency degradation is graceful where it counts.** Google Directions → bounded 3.5 s wait then haversine (documented exception). Stripe → typed `CardError`/`RateLimitError` handlers at each call site. Supabase 5xx → typed `DatabaseError` → sanitized 503. FCM → best-effort, WS is the primary channel.

### What the end user sees — gaps

| Gap | Evidence | Fix |
|---|---|---|
| Admin dashboard forwards backend `detail` verbatim into 159 toast sites, and never surfaces the `X-Request-ID` the backend already emits, so support has no correlator | `admin-dashboard/src/lib/api/client.ts:165,187-193`; 159 `description: …message` sites | Read `x-request-id` in `client.ts`, attach to the thrown error, render it as mono text in the destructive toast |
| 4xx `detail` is passthrough by design, so the 31 `str(e)` sites (import/backfill routes with file paths, row contents, SIN/DOB column names) reach browser history and Vercel logs | `utils/error_handling.py:739` | Extend the sanitizer to 4xx under `routes/admin/*`, or raise `SpinrException` with safe `message` + raw text in `details` |
| Driver never learns the rider's payment outcome at trip end | `grep payment_failed driver-app` → 0 | Emit a `payment_settled`/`payment_failed` WS event to the driver; show a neutral "payment being processed" state |
| Driver told nothing when an offer expired if the WS push fails (warning-level) | `matching.py:1488, 1604` | `logger.error` + Sentry with `domain=dispatch`, plus a metric so it is alertable |
| 21 hand-rolled `Authorization` headers on blob/PDF/CSV paths bypass the wrapper's 401→refresh, so after the 1-hour admin token lapses a download is a dead link | `lib/api/data-transfer.ts:199…`, `drivers.ts:124…` | Add `requestBlob()` to `client.ts`, replace the 21 call sites |
| `rider-app/app/ride-status.tsx` has no retry affordance; a failed `fetchRide` is a toast only | `ride-status.tsx:134` | Add a retry button bound to the existing `fetchRide` |

### What the admin / on-call gets — the structural gaps

| Gap | Evidence | Why it matters | Fix |
|---|---|---|---|
| **Two logging systems, one bridge missing.** 248 modules use stdlib `logging.getLogger`, 39 use loguru; loguru has a JSON sink in production (`server.py:469`) but there is no `InterceptHandler`, so the majority of log lines are unstructured | `grep -rl 'logging.getLogger' backend` | Loki/Grafana queries written against the JSON schema miss most of the codebase | ~15-line `InterceptHandler` set as `logging.root.handlers`; no call-site changes |
| **No distributed tracing.** `X-Trace-ID` is emitted but nothing propagates it into Supabase, Redis, Stripe, or WS fan-out; `opentelemetry` absent from `requirements.in` | `core/middleware.py:478` | "Why was this dispatch 2.4 s" is answered by grepping logs across 16 processes | `opentelemetry-instrumentation-{fastapi,httpx,redis,psycopg}` → OTLP → Grafana Tempo through the existing Alloy agent in `metrics-agent/` |
| **Alerts written, not loaded.** `metrics-agent/grafana/alert-rules.yaml` has 2 rules (dispatch P95, payment failure) and its header says "NOT YET LOADED"; nothing for `spinr_db_direct_pool_*`, `spinr_insurance_period_write_failed_total` (C55), or loop staleness beyond Slack | `alert-rules.yaml:9`, `monitoring/synthetic-checks.yaml:6-9` | Every SLA row in CLAUDE.md is unalerted | Provision via Grafana Cloud as-code; add multi-window burn-rate rules; route SEV-1 to PagerDuty as `docs/runbooks/on-call.md` promises |
| **Watchdog alerts, never restarts.** A loop whose task dies stays dead until redeploy | `core/lifespan.py:769-790` | 40 loops × 16 processes with no self-heal | Respawn with backoff and a `spinr_loop_restarts_total{loop}` counter |
| **Swallow census.** 217–222 `except Exception → logger.warning`; the dangerous ones are the two money flags above and the two dispatch sites | see Critical #5, #10 | CLAUDE.md's own rule is `logger.error` + surface | Semgrep rule failing CI on `except Exception` + `warning/pass/continue` under `routes/{rides,payments,wallet,webhooks,auth}` and `services/{payment,dispatch,corporate}*` |
| **Push retry burns quota forever.** FCM `UNREGISTERED` is treated like a transient failure; no dead-token cleanup | `utils/push_retry.py:227-272` | Every notification to a stale token runs the full 5-attempt/31-min cycle | Null `fcm_token` on `UNREGISTERED`/`INVALID_ARGUMENT` |
| **Mobile console noise.** 78 + 120 ungated `console.*` including the WS URL with user id; `shared/utils/logger.ts` exists but is unused there; no `babel-plugin-transform-remove-console` | `useRiderSocket.ts:274,315`, `client.ts:1072` | `adb logcat` sees everything | Add the babel plugin (keep `error`), or migrate to the shared logger |
| **Five telemetry SDKs on mobile.** Sentry, Crashlytics, LogRocket, `expo-observe`; `expo-insights` and the Firebase web SDK are installed with zero imports | `package.json` both apps | Five places PII scrubbing must be right, plus bundle cost | Keep Sentry + Crashlytics; remove the rest |

### Dependency-degradation matrix (verified from code)

| Dependency down | Behaviour | User-visible? |
|---|---|---|
| Redis (generic `REDIS_URL`) | In-process dict fallback; leader locks fail open; offer cooldown lost; OTP lockout resets per process; startup does **not** fail in production | No message; silent correctness drift |
| Google Directions | 3.5 s bounded wait → haversine fallback tagged for reconciliation | Rider sees a price, possibly under-priced |
| Stripe | Typed decline/retry messages; capture happens before the `paid` write | Clear message; ride never marked paid without money moving |
| Supabase 5xx | `DatabaseError` after retry → sanitized 503 | Generic "service unavailable" |
| FCM | Best-effort; WS primary | Silent; no per-failure-type metric |
| Direct pool (flag on) closed/exhausted | Falls back to PostgREST with an error log; `spinr_db_direct_pool_*` metrics emitted but unalerted | None |

Uber/Lyft comparison, where it changes the advice: one trace per ride (Jaeger at Uber) and SLO burn-rate paging are the two things that turn the existing histograms into an on-call tool. Structured event logs already exist here; the gap is coverage, not existence.

---

## 🐢 Performance Bottlenecks & Optimizations

Round-trip counts are from reading the code, not from a profiler. `loadtest/locustfile.py` gates only two of the eight SLA paths and is not in any workflow.

| SLA path (target) | Round trips today | Worst-case driver | Evidence |
|---|---|---|---|
| Dispatch offer → notification (< 2 s) | ~10 fixed (ride, settings, area, drivers ≤500, presence MGET, skip MGET, Distance Matrix, rider, incentives, quests) **+ 2N serial** (claim per driver, insurance write per driver) **+ N serial WS sends** | Three up-to-10-iteration serial loops inside one budget; direct-pool path collapses claim+insert+insurance to 1 call but is flag-off | `matching.py:1029-1049, 1112-1113, 1269-1270` |
| Fare estimate (< 300 ms, accepted 3.5 s exception) | 7–9 DB/Redis + Directions wait | One **uncached full `service_areas` scan per estimate**; `app_settings` uses the 60 s TTL cache, `service_areas` does not | `routes/rides/estimates.py:185`, `settings_loader.py:17-54` |
| Settlement (< 1 s) | 2–4 DB + inline Stripe capture | Stripe latency, inherent | `services/payment_service.py:1695-1860, 1953` |
| WS fan-out (< 100 ms) | 1–2 Redis | Targeted keys; `broadcast()` has no live callers; admin location fan-out is throttled | `utils/ws_pubsub.py:181-209`, `socket_manager.py:461` |
| Driver location write (< 150 ms) | 3–4 DB + 1 Redis gate | Serial `driver → active ride → gate → marker` chain; gate coalesces to one write per 3 s cross-replica (good) | `routes/drivers/location.py:167-260`, `utils/location_write_gate.py:189-244` |
| Auth refresh (< 200 ms) | **4 serial DB round trips** (find token → find user → insert new → update old) | ID-chained; the old-row update could run in parallel with a client-minted id | `routes/auth.py:1696-1759`, `utils/refresh_tokens.py:157-171` |
| Stripe webhook (< 500 ms) | 1 cached settings read + 1 claim insert + 1–3 finds; local signature verify | Fine | `routes/webhooks.py:545-617` |

### Ranked bottlenecks

1. **Serial per-driver claim on the live path.** Same shape three times (claim, insurance write, WS send). *Fix now:* `asyncio.gather` the WS sends (no ordering dependency) and the insurance writes; *fix durably:* flip `dispatch_direct_pool_enabled` after T16 staging data. Uber's batch-claim-as-one-write is what migration 403 already is.
2. **DB access is still PostgREST-over-HTTP through a 64-thread pool** (`_base.py:164-165`, `DB_THREAD_POOL_SIZE` default 64) for everything except the flag-off claim path. Every request that does 4 reads and 2 writes costs 6 HTTP round trips and 6 thread handoffs; the pool saturates before CPU on `shared-cpu-1x`. The new `run_sync` queue-wait histograms exist precisely to prove this. *Fix:* extend `dispatch_pool` to ride state transitions (accept/arrive/start/complete + insurance row in one transaction), location marker write, and settlement. Keep supabase-py for admin CRUD.
3. **Candidate search is a bounding box + Python haversine** (`matching.py`, 500-row cap) while a GiST index on `location_geog` has existed since migration 170 and an H3 Redis index now exists but is dark, unreconciled, and partially unwired (C53). *Fix:* finish C53, spawn the reconciler, run `dispatch_geo_provider=shadow` for a week, then `h3`. This removes the DB from the dispatch read path, which is the Uber pattern scaled down.
4. **`service_areas` re-read per estimate.** Put it behind the same 60 s TTL cache as `app_settings`, invalidated from the admin write path. One-line class of fix, on the path with the tightest target.
5. **Auth refresh chain.** Mint the new token id client-side so insert-new and revoke-old run under one `gather`; 4 round trips → effectively 2.
6. **Admin aggregation with huge explicit caps.** Only 7 `get_rows` calls under `routes/admin/` truly rely on the 1,000 default; the real risk is `limit=50000` (`drivers.py:2755`, `subscriptions.py:192`, `messaging.py:59-105` ×6) and `limit=10000` (`faqs.py:205-215`, `drivers.py:1489,2733,2852`) used to build broadcast audiences and reports in Python. *Fix:* `count_documents` / SQL aggregation RPCs for audience size; cursor pagination for lists.
7. **Admin client rendering.** `earnings/page.tsx:1073` pulls 500 rides into recharts; `subscriptions/page.tsx:508,601` fetches an unbounded list and slices client-side; 0 virtualization libraries; 13 static `recharts` importers, 8 `maplibre-gl` importers (3 lazy); 20 `<img>` vs 1 `next/image`. The drivers/users/corporate/audit-logs pages already do `PAGE_SIZE+1` server pagination correctly — copy that pattern.
8. **Rider WS reconnect drops chat.** The backend supports `?last_seq=` replay (`routes/websocket.py:643-658`) and the driver app uses it; the rider app does not (`useRiderSocket.ts:258`), so messages in the reconnect gap are lost. One query-string change.
9. **Mobile is not the bottleneck.** Driver location cadence (2 s/5 m on trip, 4 s/10 m idle, 30 s/50 m background idle) with SQLite batching to `/drivers/location-batch` at 25 points or 10 s is well designed. React Query `staleTime` 60 s default with `activeRide` at 0 is correct.

---

## 💡 Tech Stack & Architecture Recommendations

| Layer | Current | Verdict | Recommendation | Why |
|---|---|---|---|---|
| API framework | FastAPI 0.141 / Pydantic 2 / uvicorn ×2 / uvloop | Keep | — | Right choice; problems are around it |
| DB driver | supabase-py (PostgREST) via 64-thread pool; **psycopg 3 pool for dispatch claim (flag-off)** | **Extend** | Move ride transitions, location marker, settlement onto `dispatch_pool`-style transactions; keep PostgREST for admin CRUD | Transactions across `rides` + `drivers` + `driver_insurance_periods`; no thread pool; 2–5× fewer round trips |
| Geo / supply index | Bounding box + Python haversine; H3 Redis index + PostGIS RPC dark | **Finish and enable** | C53 → reconciler spawned → shadow → h3. Redis `GEOSEARCH`/H3 for live positions, Postgres validated at claim only | Uber DISCO/H3 pattern; the index is already built and paid for |
| Background work | 40 asyncio loops in every API process; `worker.py` + outbox exist but unwired | **Wire, then replace** | Wire process roles + Fly `[processes]` now; then arq (Redis leases, cron syntax) for loop bodies; Temporal only if payout/settlement sagas outgrow arq, decided by ADR | Isolation from request handling; one leader by construction; Uber's Cadence exists for this reason |
| Scheduler / leader | Redis `SET NX`, fails open | Replace with above | Until then: fail closed for `stuck_ride_sweeper` and any loop that writes ride state | Fail-open on ride-state loops is the wrong default |
| Realtime | In-process registry + one Redis channel, seq numbers + 50-entry/300 s replay outbox | Keep, finish | Rider `?last_seq=`; per-connection outbound queue with drop-oldest for location | Already close to parity with Uber/Lyft's sequenced ride streams |
| Routing / ETA | Google Directions + Distance Matrix inline; `deploy/osrm/` exists, unused | **Add** | Self-hosted OSRM/Valhalla for estimates and ranking ETAs; Google only for the quote-locked confirm | Removes most of the 3.5 s worst case and most of the Maps bill |
| Cache | Redis for presence/locks; `app_settings` 60 s in-process TTL | Add | `service_areas`, `vehicle_types`, fare config behind the same TTL + pub/sub invalidation | Read per request today |
| Feature flags | `app_settings` booleans; read failure "assumes off" (or on, for the kill-switch) | **Replace** | Unleash / GrowthBook / Flagsmith with server + RN + Next SDKs, last-known-good defaults, percentage + per-company targeting | CLAUDE.md mandates dark launches the mechanism cannot do; three dark launches this month each needed a settings row |
| Tracing | None | **Add** | OpenTelemetry → Grafana Tempo via existing Alloy agent; propagate `traceparent` into WS payloads and mobile | Only way to defend the SLA table |
| Logs | loguru JSON + stdlib plain (248 files) | Fix | `InterceptHandler`; ship to Loki via Alloy; 30-day retention | One schema, one query language |
| Alerting | 2 Grafana rules in repo, unloaded; Slack for loop staleness | **Provision** | Load the rules; add direct-pool, insurance-write, loop-restart, and burn-rate rules; PagerDuty for SEV-1 | Everything is currently a dashboard nobody is paged from |
| Contract | Hand-written `shared/types`; `shared/build-types` holds only `peer-deps.d.ts` | **Add** | Export `openapi.json` in CI, `openapi-typescript` → `shared/build-types`, fail PR on drift | ~940 explicit `any` is the symptom |
| Layering | 100 of 128 route files import `db_supabase` directly; no `import-linter` | Add | `import-linter` contracts: routes → services → repositories; start warning, ratchet per package | Enforces the service layer CLAUDE.md describes |
| Packaging | Two import roots, 1,012 dual-import blocks | Fix | `backend/pyproject.toml` + `pip install -e .`; one mechanical PR removes all 1,012 | Retires a rule that exists only to protect a packaging accident |
| Payments | Stripe + Connect, idempotent, pre-auth, ledger, outbox (dark) | Keep | Enable outbox receipts once the worker runs; DLQ table for webhook handler failures; idempotency-key lint on every `stripe.*.create/modify` | Already strong; 2 keys missing |
| Mobile | Expo 57 / RN 0.86 / React 19, EAS, OTA | Keep, trim | Remove LogRocket (or default-off), `expo-insights`, `expo-observe`, Firebase web SDK; `babel-plugin-transform-remove-console`; MMKV-encrypted store for `activeRide`/`offline_queue`; server-supplied polyline so the Maps key can be SDK-restricted; CI hash of native deps per `runtimeVersion` | PII surface, bundle, key exposure |
| Admin | Next 16 / React 19 / Tailwind 4 / shadcn / zustand / zod / recharts / maplibre | Keep, harden | `jose` `jwtVerify` in middleware or Cloudflare Access in front; `requestBlob()`; request-id in toasts; `usePagedQuery` hook; virtualized tables; `next/image` | Uber/Lyft internal tools sit behind SSO proxies and paginate server-side |
| AI assistant | Anthropic + OpenAI + Gemini adapters (`backend/ai/providers/`) | Trim | One primary + one fallback; keep the PII-scrub gate | Three SDKs is audit surface |
| Hosting | Fly `yyz` primary (rolling, no rollback step), Railway standby drifting (C5, consciously deferred) | Fix | `release_command`, rollback on health failure, staging app, `bluegreen` for the app group | A documented standby that does not build is a false safety signal |
| CI | 35 workflows, all 208 actions SHA-pinned, real-Postgres tiers, scoped coverage floors | Keep, extend | Migrations in deploy; OpenAPI drift gate; `ci-guardrails.yml`/`security-gates.yml` onto `detect-changes` (only `ci.yml` uses it); Maestro nightly + label; loadtest nightly with a diffable baseline | Gates exist; ordering and contract are missing |
| Secrets | Stripe/Twilio/Maps keys in `app_settings`, masked in admin UI | Reconsider | Fly secrets + `/internal/reload-secrets` (super_admin, audited); admin UI as write-through | Blast radius of a DB read leak; one fewer read per settlement |

---

## 🛠️ Maintainability & Code Smells

1. **God files.** Seven backend files over 2,000 lines (`routes/admin/drivers.py` 4,334, `routes/admin/rides.py` 4,030 — the two files this review's Blocker #1 lives in). Four frontend files over 1,900. Split by sub-resource with the existing sub-router pattern; split admin pages into feature folders.
2. **`utils/` is 157 modules.** Cross-cutting helpers (`crypto`, `metrics`, `redis_client`) sit beside loop bodies (`auto_payout`, `stale_p3_closer`, `t4a_annual_job`) and domain logic (`route_reconstruction_projection`). Create `backend/jobs/` and `backend/domain/<area>/`.
3. **Layering exists in name only.** 100 of 128 route files import `db_supabase` directly; `services/` is 24k lines against 74k in `routes/`. `import-linter` makes the intended layering a build check.
4. **1,012 dual-import blocks** protecting two import roots. Fix packaging, delete the rule.
5. **Legacy import code in the production import graph.** 15 import/backfill/correction services with 253 `print()`s, `diagnose_*.py`, `list_users.py`. Move under `backend/scripts/legacy/` after the Oct 31 decommission.
6. **Documentation drift that misleads reviewers.** `ARCHITECTURE.md` says Expo SDK 54 and Railway; `CLAUDE.md` says corporate coverage is "not yet enforced" (it is, blocking, scoped); `CLAUDE.md` names `_require_ride_in_state()` as the mandatory guard but it has **zero production callers** — production uses inline conditional updates, which are stronger; `SPINR_CODE_REVIEW.md` at the root lists 16 criticals that are remediated; `test_payout_toctou.py` is cited as "the model" but executes no code (see Testing). `CLAUDE.md` is 533 lines of rules interleaved with incident history.
7. **Repo root clutter.** Four `.docx/.csv`, one ad-hoc `.sql`, plus `frontend/`, `plans/`, `reports/`, `test_reports/` committed. Move review artifacts to `docs/audit/`, delete `frontend/`.
8. **Duplication.** Admin: 14 local `formatDate/formatCurrency`, 8 hand-rolled CSV `Blob` sites bypassing `lib/export-csv.ts` (which has the formula-injection guard), identical pagination scaffold in 4 pages. Mobile: 8 files duplicated per app and diverged (`BrandSplash`, `CancelReasonSheet`, `useAuth`, `crashlytics`, 3 zod schemas). Backend: 32 local `_d/_round/_money` helpers, 57 copies of `raise HTTPException(404, "Ride not found")`.
9. **Dead code.** `rider-app/utils/apiClient.ts` and `driver-app/utils/apiClient.ts` (weaker refresh pattern, 0 importers), `admin …/components/driver-map.tsx` (imports maplibre, 0 importers), `analytics-provider.tsx`, `VoltraRideActivity.tsx`, `Earnings{Bar,Line}Chart.tsx`, `services/dispatch_candidates.py` (0 callers until the geo flag flips), `match_and_claim_driver` RPC (migrations 77/80, 0 callers).
10. **Type and lint discipline.** `strict: true` everywhere, yet ~940 explicit `any`; admin `--max-warnings 1751` with `no-explicit-any` off; `expo lint` with no budget. A `lint-staged` ratchet (`--max-warnings 0` on changed files) is the only way these numbers go down.
11. **Migrations.** 492 files, 68 shared numeric prefixes; 402 superseded by 403 within 24 h because the append-only rule forbids editing merged files. CHECK B now catches same-PR collisions; the nightly duplicate checker is a symptom, not a control. Gaps are cosmetic; collisions across concurrent PRs remain possible.
12. **Process noise.** 935 change-log entries in `docs/change-log/` is excellent discipline and also unreadable without an index; `ACTION_ITEMS.md` is 19,896 lines.

---

## 🧪 Testing & QA (Missing Edge Cases)

**What is genuinely good:** 12,839 backend tests; scoped, blocking per-package coverage floors (money ≥90, corporate ≥80, admin ≥70) in `ci-guardrails.yml`; real-Postgres tiers for RLS (11 policies) and the direct-pool claim RPC now in CI; strong money tests that call the real settle functions (`test_atomic_settle.py`, `test_corporate_webhook.py`); race tests for concurrent accept, offer timeout vs accept, Redis-down skip-key, scheduled-ride double dispatch, GPS teleport, refresh-token reuse, LIKE escaping, duplicate `POST /rides`; mobile tests for booking, offer accept, payment sheet, SOS, and background location; an ESLint rule that fails on raw `error.message` in alerts.

**What is hollow or missing:**

| ID | Risk | Gap | Evidence |
|---|---|---|---|
| T1 | High | Two "model" money tests execute no code: they read source files as text and assert substring order (`_SQL.index("FOR UPDATE") < …`) | `tests/test_payout_toctou.py`, `tests/test_wallet_apply_delta_contract.py` |
| T2 | High | `wallet_apply_delta` / `corporate_wallet_apply_delta` — the two RPCs that move real money — run against real Postgres in **neither** `tests/rls` nor `tests/direct_pool` | `backend/tests/direct_pool/`, `backend/tests/rls/` |
| T3 | High | No test that admin force-cancel rejects `in_progress` (3 files reference the handler, none cover that state) | `grep admin_cancel_ride backend/tests` |
| T4 | High | `payment_intent.succeeded` arriving before the ride is `completed` (ordering) — missing | only refund ordering in `test_routes_webhooks_coverage.py` |
| T5 | Medium | Admin e2e runs against a Next build with `BACKEND_URL=localhost:8000` and no backend; login is `page.route`-mocked, every table under test is empty | `ci.yml:472-486`, `admin-dashboard/e2e/auth.setup.ts:8-21` |
| T6 | Medium | Admin vitest gate 10–19%; RBAC hook and API client error mapping have one test each; 26 of 59 tests are zod schemas | `vitest.config.ts:31-36` |
| T7 | Medium | Maestro flows exist for both apps but run only on `workflow_dispatch`/label; never nightly | `maestro-e2e.yml:33-40` |
| T8 | Medium | Load test is not in any workflow, asserts no threshold, and has no diffable baseline; gates 2 of 8 SLA paths | `loadtest/locustfile.py`, `loadtest/results/` |
| T9 | Medium | No contract test between the FastAPI OpenAPI schema and `shared/types` / admin `lib/api` | `shared/build-types/` contains only `peer-deps.d.ts` |
| T10 | Low | No property-based tests over the ride state machine; `test_ride_state_machine.py` has 14 tests covering the guard helper only | `grep -rl hypothesis backend/tests` → 0 |
| T11 | Low | Visual regression job self-skips (no baselines, B38); Storybook has 3 stories | `ci.yml:503`, `find src -name '*.stories.tsx'` |

**Edge-case matrix (named-test evidence):**

| Case | Status |
|---|---|
| Two drivers accept within 100 ms → one 200, one 409 + `ride_taken` | Exists (`test_dispatch_claim_parity.py`) |
| Offer times out at T, driver accepts at T+50 ms | Exists (`test_offer_timeout.py`) |
| Redis down → skip-key write fails → same driver re-offered | Exists as a test; behaviour is still warning-only in code |
| Rider cancels at `driver_arrived` with no-show fee | Exists (`test_fee_wallet_atomic.py`, `test_c2_driver_cancel_atomic.py`) |
| Driver app killed mid-`in_progress` vs stale-P3 closer | Exists (`test_stale_p3_closer.py`) |
| Scheduled ride dispatched by two replicas | Exists (atomic claim, `scheduled_rides.py:445-478`) |
| Scheduled ride across a DST boundary (riders scheduling from other zones) | **Missing** |
| Duplicate Stripe webhook after `unclaim_stripe_event` | Partial (tests the unclaim call, not replay-then-reprocess) |
| `payment_intent.succeeded` before ride `completed` | **Missing** |
| Tip added after payout transferred | Exists (`test_charge_late_*_tip.py`) |
| Corporate allowance exhausted mid-ride → master wallet | Exists |
| Surge change with expired `estimate_token` | Exists (`test_e16_surge_boundary.py:183`) |
| Partial refund then dispute on the same charge | Exists (`test_dispute_refund_cents.py`) |
| GPS jump > 200 km/h, future client timestamp | Exists (`test_gps_filtering.py`, `test_location_integrity.py`) |
| Refresh-token reuse after rotation | Exists (`test_refresh_token_reuse_detection.py`) |
| WS outlives token expiry | Exists via heartbeat token-version revalidation (`test_websocket_coverage.py:640,666`) |
| Emoji / RTL / 10 kB address through `$regex` | Exists (`test_base_like_escape.py`) |
| Duplicate `POST /rides` with and without `Idempotency-Key` | Exists (`test_e8_duplicate_ride.py`; DB partial unique index is the real backstop) |
| OTP brute force across IPs | **Missing** (lockout tests are per-phone) |
| Admin force-cancel / force-complete on a live ride | **Missing** |
| Mobile: 30-min background resume, WS/push/REST disagree | **Missing** |
| Driver background-location permission revoked mid-trip | **Missing** (code path only `console.warn`s) |
| Rider push tap on cold start before `fetchRide` | **Missing** |
| Direct-pool exhaustion under load | **Missing** (code-reasoned only; T16 staging run was a pre-C50 baseline) |

Uber/Lyft comparison: the ordering and race rows above are the class of bug they catch with shadow-traffic replay and canary analysis, not unit tests. Spinr has no shadow or canary mechanism, so these are only as safe as the explicit tests written for them — which is why T1–T4 are ranked High.

---

## 📈 Manager's Verdict (Overall summary of code health)

**Overall: B-.** Unchanged from 09-01 in letter, but the shape has moved. The team shipped the hardest structural fix (a real transactional claim path on a real Postgres driver) in two days, with a real-Postgres test tier, per-phase metrics, and a rollback flag — that is Uber-grade engineering discipline. In the same window it also shipped three dark launches (H3, outbox, worker split) that read as done and are not wired, one of which needed two follow-up PRs just to import. The risk profile has shifted from "topology caps us at one city" to "the codebase's own description of itself is drifting from what runs," which is a harder problem for a small team because it wastes the reviewers' attention on things that are already fixed and hides the things that are not.

The conventions in `CLAUDE.md` remain unusually specific and are mostly enforced. Money paths are Decimal, idempotent, capture-before-write, and 0% commission is intact (`platform_share` appears nowhere). The error envelope, PII scrubber, refresh-token theft cascade, and WS sequence replay are better than most Series-A ride-share stacks. What is missing is the operational layer that makes the good code safe to change: schema in deploy, a staging gate, a rollback step, loops out of the API, tracing, loaded alerts, and a middleware that verifies signatures.

| Dimension | 09-01 | 09-03 | One line |
|---|---|---|---|
| Correctness | B+ | **B** | Admin cancel of an in-progress ride is a live state-machine hole; C54 and the timeout-revert TOCTOU are on the path that runs today |
| Security | B+ | **B+** | Auth/OTP/webhook/rate-limit posture is strong; decode-only admin middleware, unaudited export, replay-without-redaction, and DB-stored keys are the remaining smell |
| Error handling / telemetry | B | **B** | Envelope and scrubber excellent; two money flags fail silently; no tracing; alerts unloaded; logs half-structured |
| Performance | C+ | **B-** | The fix for the worst path is built and off; `service_areas` and auth refresh are cheap wins |
| Architecture | C+ | **C+** | Direct pool is the right direction; worker split and H3 are inert; flags are still a settings table |
| Maintainability | C | **C** | 4k-line files, 157 utils, 1,012 dual imports, 1,751 lint budget, docs describing code that does not run |
| Testing | B | **B** | Real-Postgres tiers in CI is real progress; two "model" tests are theatre; wallet RPCs untested on Postgres |
| Process | B+ | **B** | Migrations manual, no staging, no rollback on the primary; the standby that has rollback does not build |

### The plan

Each item has a verification step. Nothing here requires Kubernetes, Kafka, microservices, or an H3 mesh — those would lower the grade by adding surface the team cannot staff.

**Phase 0 — this week (stop the bleeding, all < 1 day each, no flags needed)**
1. `admin_cancel_ride`: allowed-state check + conditional update + Period-1 transition + test → verify: new test fails on HEAD, passes after.
2. Fail closed on `corporate_billing_enabled` read error; `logger.error` + counter on `ledger_atomic_settle_enabled` fallback → verify: unit test with the settings read raising.
3. PostgREST claim loop: `try/except` releasing `claimed_drivers`; add `status`+`driver_id` to the single-offer revert filter → verify: extend `test_dispatch_db_errors.py`.
4. `deploy-fly.yml`: `flyctl releases rollback` on health-probe failure → verify: dry-run against staging once it exists; until then, a workflow syntax check.
5. Admin middleware: `jose.jwtVerify` → verify: Playwright test with a forged cookie hitting `/dashboard`.
6. Audit row on `admin_export_filtered_rides` → verify: test asserting `audit_log` call before the query.
7. LogRocket: `redactionTags` + privacy screen on every ride screen, or default the flag off → verify: config diff; device capture cannot be verified here.
8. `shouldDehydrateQuery` excluding `activeRide` and `auth` → verify: unit test on the persister options.
9. Rider `?last_seq=` → verify: mirror the driver hook's test.

**Phase 1 — weeks 2–4 (make the dark launches real, in the order they were built)**
10. Wire `should_spawn_on_api()` into `_spawn`; Fly `[processes] app/worker`; `scale count app=8 worker=1`; spawn `h3_index_reconciler`; startup assertion that catalog == spawned ∪ worker-owned → verify: `test_lifespan_watchdog_coverage.py` extended; `flyctl status` shows both groups.
11. Finish C53; run `dispatch_geo_provider=shadow` for a week; compare `shadow_*` metrics → verify: zero `shadow_skipped`, in-radius parity.
12. Provision staging per `deploy-backend-staging.yml`'s runbook; deploy the C50 branch there; run T16 for real → verify: A/B P95 rows in `loadtest/results/`.
13. Fly `release_command` running `run_migrations.py` (refuse `NEVER_APPLY`, fail deploy on error); expand/contract rule in `backend/migrations/CLAUDE.md` → verify: a deliberate no-op migration deploys through the pipeline.
14. Load the Grafana alert rules; add direct-pool, insurance-write, loop-restart, burn-rate rules; PagerDuty route → verify: a synthetic breach pages.

**Phase 2 — weeks 4–8 (turn histograms into an on-call tool)**
15. `InterceptHandler`; Loki via Alloy → verify: one Loki query returns stdlib and loguru lines with the same schema.
16. OpenTelemetry (FastAPI, httpx, redis, psycopg) → Tempo → verify: one trace spans estimate → dispatch → WS send.
17. Semgrep rules: `except Exception` + `warning/pass` in money/dispatch/auth dirs; `detail=str(e)`; float on money; router without `require_module` → verify: rule fires on a fixture, blocks CI.
18. `service_areas` behind the 60 s cache; auth-refresh `gather`; WS sends gathered in dispatch → verify: `perf_rides_*` JSON before/after; run_sync histograms.
19. Watchdog respawn + `spinr_loop_restarts_total`; FCM dead-token cleanup → verify: kill a loop task in a test, assert respawn.

**Phase 3 — weeks 8–14 (contracts, flags, tests that execute)**
20. Flip `dispatch_direct_pool_enabled` on in staging, then production canary by service area → verify: `spinr_dispatch_claim_path_total{path}` and P95 rows.
21. `tests/direct_pool/test_wallet_apply_delta.py` and `…corporate…` (concurrent delta, floor clamp, idempotent replay); rewrite T1's two theatre tests as executed tests; add T3/T4 → verify: the new tests fail on a deliberately broken RPC.
22. OpenAPI export in CI → `openapi-typescript` → `shared/build-types`; drift gate → verify: PR that changes a response model fails without regenerating.
23. Feature-flag service (Unleash or GrowthBook) with last-known-good defaults; migrate the six dark-launch flags and the corporate kill-switch → verify: a 5% canary on one flag.
24. `import-linter` (warning → blocking per package); `pyproject.toml` + `pip install -e .`; one mechanical PR removing 1,012 dual-import blocks → verify: full suite green on both invocation styles removed.
25. Admin: `requestBlob()`, request-id in toasts, `usePagedQuery`, virtualized tables, vitest per-directory floors (`hooks`, `lib/api`, `store` ≥ 80%) → verify: `npm run build` + vitest thresholds.

**Phase 4 — weeks 14–26 (Uber-shaped where it pays)**
26. arq lease scheduler replacing `_spawn` loops (bodies unchanged); Temporal only by ADR if payout/settlement sagas need it.
27. Redis GEO / H3 as the dispatch read path; OSRM for estimates; Google for the locked confirm.
28. Secrets to Fly + `/internal/reload-secrets`; step-up OTP on payout-destination and admin role changes; App Check required on mutating endpoints behind a canary flag.
29. Split the seven 2k+ backend files and four 2k+ pages; re-home `utils/`; retire legacy import services after the Oct 31 decommission; delete `frontend/` and root artifacts; fix `ARCHITECTURE.md`; split `CLAUDE.md` into rules and `docs/history/`.
30. Maestro nightly + label; loadtest nightly with a diffable baseline; mutation sampling on `fare_service.py` and `utils/money.py`; regrade.

Every phase is independently shippable and independently revertible.

### What was NOT verified

Live Supabase index/RLS state, Fly machine config and whether Railway currently builds, Grafana/Alloy runtime health, Stripe dashboard settings, actual P95s (no profiler or load test was run; T16's only run predates C50), LogRocket's on-device capture, OTA behaviour, mobile bundle size, branch-protection required-check lists (server-side), and whether `settings.ENV` is ever set to a non-canonical string in any deploy config. Those need production access, not code reading.
