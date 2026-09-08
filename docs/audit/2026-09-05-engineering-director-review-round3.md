# Spinr — Engineering Director Review, Round 3 (read-only, 2026-09-05)

**Scope:** whole repository on branch `claude/rideshare-code-review-2pzhgv` at `d32ba90`
(backend, rider-app, driver-app, admin-dashboard, shared, CI/CD, infra, tests).
**Method:** first-hand code reading and repo-wide census, plus four domain deep
dives (data layer/performance, money paths, dispatch/realtime, mobile). No code
was changed. Every claim below cites a file and line that was read today;
anything that could only be confirmed against a live system (Supabase, Fly,
Stripe, Grafana, Railway) is marked *not verified*.
**Baseline:** this is the third director-level pass in five days. It does not
repeat `docs/audit/2026-09-01-engineering-director-teardown.md`; it re-verifies
that teardown's findings against today's code, adds what it missed, and grades
the four days of movement since (247 commits, 137 PR merges).
**Comparison frame:** how Uber/Lyft-class platforms solve the same problem,
scaled to what a Saskatchewan-first, 0 %-commission operator needs. "Know which
of Uber's problems you already have" — not "do what Uber does".

---

## 0. Snapshot (verified today)

| Metric | 2026-09-01 | 2026-09-05 | Trend |
|---|---|---|---|
| Backend Python, non-test files | 436 | 451 | + |
| Backend test files / `def test_` | 815 / 12,534 | 814 / 12,925 | + |
| Migrations / duplicate numeric prefixes | 484 / ~60 | 496 / 66 | worse |
| Startup loops | 54 names | 41 catalogued (`core/background_loop_registry.py`: 11 `api`, 27 `deferred`, 3 `worker_wave1`); 46 `_spawn()` calls | split coded, not deployed |
| `except ImportError` dual-import blocks | 984 | 1,020 | +9/day |
| `except Exception` (non-test) → `logger.warning` / `pass` | 1,556 → 216 / 18 | 1,634 → 215 / 17 | worse |
| `detail=str(e)` in routes | 33 | 30 | slightly better |
| `print(` in importable non-test code | 253 | 261 | worse |
| Backend files > 800 lines | 7 over 2,000 | 15 over 800; `routes/admin/drivers.py` 4,334, `routes/admin/rides.py` 4,171 | worse |
| Explicit `any` (admin src + apps + shared) | 451 + 515 | 1,102 | flat |
| admin ESLint budget | `--max-warnings 1751` | unchanged | — |
| Coverage gate | 60 % | 60 % (`pytest.ini` promised 65→70 in July) | — |
| RLS policies / migrations still using `FOR ALL` | — | 132 / 35 | — |
| Real-Postgres test tiers wired in CI | rls | rls + direct_pool | better |
| Admin visual regression | continue-on-error | blocking, 6 pages seeded (B38 closed 09-04) | better |
| Largest admin page | `drivers/page.tsx` 3,786 | decomposed (#4955); largest now `settings/page.tsx` 1,812 | better |
| Authorship (shallow clone, 257 commits) | — | ittalenthireca-sketch 102, Claude 94, srikumarimuddana-lab 45, dependabot 11 | — |

### Status of the 2026-09-01 criticals and the C59 plan, today

| ID | Finding | Status | Evidence |
|---|---|---|---|
| C1 | Migrations not in deploy | **OPEN** | `backend/fly.toml` has no `release_command`; `deploy-fly.yml` runs `flyctl deploy` only; staging schema at migration 371 vs repo 405+ and staging never redeployed since 2026-08-29 (`docs/audit/2026-09-02-t16-round2-…`) |
| C2 | Loops on every API replica | **PARTIAL, dark** | `backend/worker.py` + registry exist; `core/lifespan.py` never imports the registry and `_spawn()` starts all loops unconditionally; `fly.toml:56` still one `app` process; `SPINR_PROCESS_ROLE` is read nowhere. Registry has already drifted (lists `h3_index_reconciler`, which lifespan never spawns; omits `insurance_period_reconciler`, `lifespan.py:568`) |
| C3 | PostgREST via 64-thread pool | **PARTIAL, dark** | `repositories/dispatch_pool.py` (psycopg3 `AsyncConnectionPool`, Supavisor transaction mode) is wired only into the claim batch (`routes/rides/matching.py:959-1072`) behind `app_settings.dispatch_direct_pool_enabled` (default `False`, `schemas.py:700`) and `DISPATCH_POOL_DSN` (empty). Everything else is `run_sync` over PostgREST (`repositories/_base.py:161-166`) |
| C4 | Bounding-box + Python haversine | **PARTIAL** | Geo-provider framework landed (`services/dispatch_candidates.py:240`, providers legacy/shadow/postgis/h3). `schemas.py:707` defaults to `postgis` but migration `397` sets the DB column default to `legacy`, and the DB row wins (`settings_loader.py:47-51`), so the live provider is whatever the row holds. The H3 provider can never be ready: nothing on the location write path calls `upsert_driver` (`dispatch_candidates.py:349` fails over every time) |
| C5 | Flags are settings-table booleans | **OPEN, formalised** | ADR-011 records read-failure semantics; corporate kill switch now fails closed (`3d4be10`); still no targeting, percentage, or kill-switch audit |
| C6 | Admin `str(e)` leaks | **OPEN** | 30 sites (list in §1.7); WS-E plan written, not executed |
| T1 | Admin force-cancel skips period close | **CLOSED** | `e32becc`, C66 |
| T2 | Batch-claim leak on exception | **CLOSED** | `0d3729b`, `576998b` (C54) |
| T4 | Admin middleware decodes JWT without verifying | **OPEN** | `admin-dashboard/src/middleware.ts:113-140` |
| T7 | `REDIS_URL` unset fails open in prod | **OPEN** | `core/config.py:171-177`; `lifespan.py:170-178` logs and boots; only `RATE_LIMIT_REDIS_URL` hard-fails (`core/middleware.py:774-783`) |
| P4 | Admin WS fan-out per driver ping | **FIXED** | `socket_manager.py:19,472` 3 s per-driver throttle |
| P5 | Admin lists unpaginated | **MOSTLY FIXED** | `admin/rides.py:238-239` `le=100`; `admin/drivers.py:477` `le=500`; residuals in §3 |
| P8 | Redis fallback | OPEN | as T7 |

---

## 1. 🚨 Critical Issues & Security Flaws

Ordered by blast radius. None is an unauthenticated exploit. Three are process
faults that turn ordinary bugs into production incidents; the rest are code.

### 1.1 Production deploys are gated on nothing
- **Evidence.** `.github/workflows/deploy-fly.yml:1-12` triggers on `push` to `main`
  for `backend/**` with no `workflow_run` on the CI workflow and no `needs`. It
  deploys, scales to 8, and polls `/health`; there is no rollback step.
  `ACTION_ITEMS.md` C21 documents PRs merged by native auto-merge while
  `CI/CD Pipeline`, `CI Guard Rails` and `Security Gates` were still queued, and a
  2026-09-04 window in which `main` carried a real backend-test failure across 14
  consecutive merges for ~2.5 h. `main-branch-guard.yml` (#4990) is, by its own
  header, "a detection backstop, not a merge gate". `.github/CODEOWNERS` states
  the repo has exactly 2 collaborators and 0 teams; the account that authors
  most PRs cannot approve them.
- **Why it matters.** Any of those 14 merges touching `backend/**` shipped to
  production without a passing test run. With ~35 merges/day, majority
  AI-authored, on a product taking real Stripe charges, this is the single
  largest risk in the repository. Uber/Lyft-class shops treat "merge waits for
  green, deploy waits for merge, canary waits for deploy" as non-negotiable; here
  all three links are advisory.
- **Fix.** (1) Make `deploy-fly.yml` a `workflow_run` on "CI/CD Pipeline"
  completed-success for `main`, or move the deploy job into `ci.yml` behind
  `needs: [backend-test, python-dependency-audit, migration-check]`. (2) A repo
  admin sets the required-status-checks list (C21) and requires one approval;
  until a second human approver exists, require the `Claude Approvals`/Codex
  review to be restored (C7/C9). (3) Add automatic rollback: `flyctl releases
  rollback` when the post-deploy probe fails, or `strategy = "canary"` with the
  probe as the gate.

### 1.2 Schema change is still a hand-run script, and staging is 30+ migrations behind
- **Evidence.** `backend/fly.toml` has no `release_command`; `apply-supabase-schema.yml`
  is `workflow_dispatch` only; every migration runbook has a human export
  `DATABASE_URL` from the Supabase dashboard. Staging's `schema_migrations` tops
  out at 371 while the repo is at 405+; staging has had exactly one Fly release
  (2026-08-29, status `failed` but serving). 66 numeric prefixes are duplicated
  across `backend/migrations/`.
- **Why it matters.** Code that expects `dispatch_direct_pool_enabled` (migration
  401) cannot even be deployed to staging today without 500s on settings
  reads. A production deploy that lands before or after its migration is the
  classic ride-share outage; the repo already carries a runbook for two such
  events (`deploy-migration-297.md`, `deploy-migration-64-65.md`).
- **Fix.** `release_command = "python -m backend.scripts.run_migrations"` in
  `fly.toml` (fails the release on error, refuses `NEVER_APPLY` files), the DSN
  as a Fly secret, and an expand/contract rule in `backend/migrations/CLAUDE.md`
  so the previous build always runs against the new schema. Bring staging to
  parity first (T3 in the C59 plan) — nothing in §3 can be validated until then.

### 1.3 The worker tier exists but runs nowhere; every loop, including every money loop, still runs on every API replica behind a lock that fails open
- **Evidence.** `core/background_loop_registry.py` marks 27 loops `deferred` —
  `payment_retry`, `preauth_capture`, `auto_payout`, `corporate_autotopup`,
  `orphaned_hold_reconciler`, `stripe_reconcile` among them — and 3 `worker_wave1`.
  `core/lifespan.py` never consults the registry; `_spawn()` (`lifespan.py:225`)
  starts all of them on every process. `fly.toml:56` has one process group; the
  Dockerfile CMD is `uvicorn server:app`. `worker.py:71` is the only caller of
  `run_outbox_worker`, so the transactional outbox (`services/outbox.py`,
  migration 399) has no consumer in production. `utils/redis_client.py:615-647`:
  `try_acquire_leader_lock` returns `True` on any Redis error; only 5 loops use
  it, ~24 hand-roll `redis_set_nx` and every money loop re-implements the same
  fail-open (`auto_payout.py:1246-1248`, `payment_retry.py:736-744`,
  `preauth_capture.py:201-209`, `orphaned_hold_reconciler.py:247-255`);
  `corporate_autotopup.py` has no lock at all.
- **Why it matters.** A Redis blip means 16 processes (8 machines × 2 workers)
  each believe they lead. Correctness then rests entirely on idempotency keys
  and DB claims, which mostly hold, but the API event loop and its 1 GB share
  CPU with batch sweeps during the exact minute dispatch needs it. If anyone
  flips the outbox flag, receipts strand silently. Uber runs this class of work
  in Cadence/Temporal workers with leases; Spinr has designed the equivalent
  (registry, `worker.py`) and not deployed it.
- **Fix.** `[processes] app = "…server:app"`, `worker = "…worker:app"` in
  `fly.toml`, `SPINR_PROCESS_ROLE=api` on `app`, `should_spawn_on_api()` inside
  `_spawn`, a registry⇄lifespan coverage test (the watchdog self-check at
  `lifespan.py:800-822` is the template), and a `try_acquire_leader_lock_strict`
  that returns `False` on Redis error for every `deferred` money loop.

### 1.4 Admin dashboard middleware trusts an unverified JWT (C59 T4, still open)
- **Evidence.** `admin-dashboard/src/middleware.ts:113-140` — `decodeJwtPayload`
  splits and base64-decodes the token, `isTokenValid` checks only `exp`. No
  `jwtVerify`. The comment says "without verifying the signature (Edge Runtime
  compatible)".
- **Why it matters.** Impact is bounded because every data call goes to the
  backend, which verifies the token; but the routing shell, module gating in
  the UI, and any Next route or server component that reads claims from the
  cookie are fooled by a hand-made token. `jose` is Edge-compatible; the
  comment's premise is wrong.
- **Fix.** `jose.jwtVerify(token, secret)` (HS256 with `JWT_SECRET`, or JWKS if
  rotated) in the middleware; keep the backend as the authority; test a
  tampered token is redirected to `/login`.

### 1.5 `REDIS_URL` can be empty in production, and the Redis client has no socket timeouts
- **Evidence.** `core/config.py:171-177` default `""` for `REDIS_URL`,
  `RATE_LIMIT_REDIS_URL`, `WS_REDIS_URL`; `core/lifespan.py:170-178` logs ERROR
  and continues; `utils/rate_limiter.py:42-48` falls back to `memory://`.
  `utils/redis_client.py:150` `aioredis.from_url(url, …)` with no
  `socket_connect_timeout`, `socket_timeout` or `health_check_interval`; only
  `utils/location_write_gate.py:139` wraps its calls in `wait_for(…, 0.1)`.
- **Why it matters.** Empty URL: rate limits, OTP lockout, presence, leader
  locks and offer-skip keys become per-process dicts and everything "passes".
  Black-holed Redis: the presence `MGET` in every dispatch attempt
  (`matching.py:552`), every lock and every retry-token read hang for the TCP
  default, blowing the dispatch and location SLAs at once.
- **Fix.** A pydantic validator that raises in `ENV=production` when any of the
  three is empty (the file already fails fast on weak secrets); client options
  `socket_connect_timeout=1.0, socket_timeout=0.5, health_check_interval=30`;
  `asyncio.wait_for` around the dispatch presence read.

### 1.6 Pickup OTP: no per-ride attempt limit, no input validation, crashes on NULL
- **Evidence.** `routes/drivers/ride_flow.py:872-874`
  `stored_otp = ride.get("pickup_otp", ""); if not hmac.compare_digest(stored_otp, request.otp)`.
  `routes/drivers/_shared.py:314-315` `class RideOTPRequest: otp: str` — no
  length or digit constraint. No `otp_attempt`/`pickup_otp_fail` counter exists
  anywhere; the only limiter is the global default `100/minute` per IP
  (`utils/rate_limiter.py:219-221`).
- **Why it matters.** The assigned driver can brute-force a 4-digit OTP in ~50
  minutes expected from one IP and flip the ride to `in_progress` — Period 3
  insurance and a running meter — with no rider aboard. A NULL `pickup_otp`
  (legacy/admin-created ride) makes `compare_digest(None, …)` raise → 500. An
  empty stored OTP matches an empty submitted one. (Reported as major #35 in
  `SPINR_CODE_REVIEW.md`; still open.)
- **Fix.** `Field(min_length=4, max_length=6, pattern=r"^\d+$")`; a Redis counter
  `spinr:ride:{id}:otp_fail` that locks the OTP after 5 failures, alerts ops and
  notifies the rider; treat a missing OTP as 409, never 500.

### 1.7 Admin 4xx responses still echo raw exception text (30 sites)
- **Evidence.** `grep -rnE 'detail=str\(e\)|detail=f"[^"]*\{e\}' backend/routes`
  → 30, e.g. `routes/admin/legacy_sin_dob_backfill.py:99` (SIN/DOB CSV import),
  `routes/admin/driver_import.py:88,111`, `routes/admin/export_approvals.py:55,82`,
  `routes/drivers/appeals.py:79`, `routes/drivers/tax_exports.py:497` (driver-facing).
  `utils/error_handling.py:740` sanitises only `status_code >= 500`.
- **Why it matters.** Import services raise with row contents and column names;
  the text lands in browser history, Vercel logs and Sentry breadcrumbs. PIPEDA
  does not distinguish "only admins saw it". Two driver-facing sites leak
  upstream text to a contractor.
- **Fix.** Execute WS-E from `plans/2026-09-01-critical-topology-remediation-plan.md`
  (redactor in `utils/pii.py`, applied to every 4xx string detail in the handler,
  plus a guard test that fails on any new `detail=str(`).

### 1.8 `/health` couples liveness to the DB circuit breaker
- **Evidence.** `server.py:206` `/health` runs the DB `ping()`; the breaker opens
  after 5 transient failures in 30 s for 60 s (`repositories/_base.py:80-82`);
  `fly.toml` `[[http_service.checks]]` polls `/health` every 30 s, 5 s timeout.
- **Why it matters.** A 60 s Supabase hiccup makes every machine report
  unhealthy, so Fly's proxy stops routing all traffic — including WebSocket
  keep-alives and cached reads that would have worked. A degraded dependency
  becomes a full outage for the whole open window.
- **Fix.** Liveness returns 200 with `{"db": "degraded"}`; readiness stays on a
  separate path that the deploy probe (not the runtime check) uses.

### 1.9 The Railway "standby" would run 4 workers × 64 threads and all 41 loops from a stale build against the production database
- **Evidence.** `backend/Dockerfile:95` `--workers ${UVICORN_WORKERS:-4}`,
  `railway.json`; `ACTION_ITEMS.md` C5 re-confirmed deferred on 2026-09-04.
- **Why it matters.** A documented standby that is both stale and heavier than
  primary is a false safety signal; if its deploy is ever unblocked it doubles
  the unlocked-loop pressure on the same DB (§3, P4).
- **Fix.** Pin `UVICORN_WORKERS=2` and `SPINR_PROCESS_ROLE=api` in `railway.json`,
  or scale it to 0 and remove the standby claim from `CLAUDE.md`,
  `ARCHITECTURE.md` and the runbook until it is verified.

### 1.10 Money-path findings (deep dive)

**Verified from prior reviews.** Standard payouts reserve a `payouts` row before
`Transfer.create` with key `payout-transfer-{payout_id}`
(`routes/drivers/payouts.py:937-973`). `Subscription.create` now carries
`corp-sub-create-{company_id}-{customer_id}` (`services/corporate_subscription_service.py:136`);
`.modify`/`.delete` (`:212,215`) remain unkeyed but are semantically idempotent.
The money-path flag read that "assumes off" moved to `_atomic_settle_enabled`
(`services/payment_service.py:1552-1590`), returns `False` on error with a
metric, and cites ADR-011 — it falls back to the legacy non-atomic settle
rather than refusing, which is the recorded decision. The corporate kill
switch fails closed with a 503 and releases the `processing` claim
(`:970-985`). Tips after a payout are handled by design: `payable_balance` is
derived live from `rides.driver_earnings` (`routes/drivers/_shared.py:113-117`,
`earnings.py:79,147,287`) so a late tip lands in the next payout, and `/rate`
rejects post-settlement tips (`routes/rides/rating.py:69-73`).

**Done well.** `process_payment` dispatches wallet → `wallet_pay_for_ride` RPC
(`payment_service.py:853`), company allowance → allowance-then-master saga with
`apply_ride_debit_reversal` compensation (`:1103-1125`, `:1214-1225`), else card
→ `_settle_against_hold` (`routes/rides/payments.py:630-648`); the only `float(`
is inside `_f()`. Webhook wallet top-ups dedupe on `(wallet_id, reference_id, type)`
inside the row lock (`routes/webhooks.py:741-750`); `_handle_ride_invoice_paid`
refuses to ack while `processing` (`:264-291`); all three `unclaim_stripe_event`
sites (`:800,882,950`) precede any side effect. `charge.refunded` is replay-safe
by construction (delta from stored `refund_amount`, `:1085-1090`). Taxes are
`_q2(taxable × rate/100)` HALF_UP per line then summed (`features.py:802-819`)
and the receipt total is a Decimal sum of rendered lines (`routes/rides/_shared.py:560-575`).

**New findings** (all live-tested surfaces; each needs a Change Impact & Risk
Log entry per `CLAUDE.md`):

- **N1 — Major. `payment_intent.payment_failed` overwrites a paid ride.**
  `routes/webhooks.py:932-939` updates `payment_status="failed"` and swaps
  `payment_intent_id` with no guard on current status or PI match. Scenario: PI
  fails on card 1 (event A); a DB blip hits the `unclaim` at `:950` so A is
  redelivered minutes later; meanwhile the rider retries and event B settles
  the $42.50 ride `paid`. Redelivered A flips it to `failed`; the rider is
  prompted to pay again, and `settle_card` mints a new PI under a different
  key (`ride-charge-…` in `utils/stripe_charge.py:181` vs `ps-…` in
  `routes/payments.py:1339`) — nothing keys off "a succeeded PI already exists".
  Fix: filter the update on `payment_status IN ('pending','processing')` and
  `payment_intent_id IS NULL OR = this PI`; treat 0 rows as stale and ack.
- **N2 — Major. Card riders are never charged the no-show fee.**
  `routes/drivers/ride_cancel.py:367-369` debits only when
  `payment_method == "wallet"` (`wallet_apply_delta`, `:381`); there is no card
  branch, and `capture_cancellation_fee`/`charge_ancillary_fee` are called only
  from the rider-cancel path (`routes/rides/cancellation.py:166,291,650`). The
  driver is still credited `fee_driver` ($4.00 default,
  `services/cancellation_service.py:53`; `ride_cancel.py:405-411`) and the ride is
  stamped as if collected (`:425-426`); the booking hold is neither captured nor
  released. Cost: $4.00 platform-funded per card no-show plus $0.50 uncollected.
  Fix: reuse `cancellation.py:163-224`'s hold-capture → fresh-charge fallback in
  `mark_no_show`.
- **N3 — Major. A mid-trip stop edit drops the promo discount from `grand_total`.**
  `routes/rides/_shared.py:419` `grand_total = _round(new_total + fees_total + tax_amount)`
  omits `- discount`, unlike `services/fare_service.py:468`. Settlement charges
  `_q(ride.grand_total) + tip` (`routes/rides/payments.py:590-594`) and edits are
  allowed in `in_progress` (`stops.py:54-58`). Example: $20 fare, $5 promo →
  $15.75 booked; add a $3 stop → $24.15 charged instead of $19.15, while the
  refreshed snapshot still renders a −$5 line (`_shared.py:635-646`), so the
  receipt no longer matches the charge — a disclosed-line-item violation. Fix:
  subtract `_d(ride.get("discount_amount") or 0)` in `_reestimate_fare_for_stops`.
- **N4 — Major (reporting). The dispute-close ledger append is not replay-safe.**
  `services/payment_service.py:389-411` loops `balance_transactions` and calls
  `ledger_service.record_event` unconditionally; `record_event` mints a fresh
  `uuid4` per call (`services/ledger_service.py:383-385`) and no `UNIQUE`
  index exists on `financial_events`. Stuck events are re-run through
  `_dispatch_stripe_event` by the admin replay (`webhooks.py:660-668`,
  `routes/admin/stripe_events.py:65-77`), so a crash after the ledger write
  books a −$42.50 chargeback and −$15.00 fee twice. Fix: derive the ledger id
  from `(event_type, ref, balance_transaction_id)` or add that unique index
  (duplicate-key is already treated as success, `ledger_service.py:343-345`).
- **N5 — Low. Replay overwrites payment linkage.** `webhooks.py:861-866` writes
  `payment_intent_id`/`paid_at` even when `_already_settled`; an in-app-settled
  ride that also receives a PaymentSheet success has its PI swapped, so later
  refund/dispute lookups by PI (`:1245-1248`) resolve the wrong charge. Skip the
  update when already settled.
- **N6 — Low. Float arithmetic on the rider-facing quote.** `features.py:893`
  `round(subtotal + fees_total + tax_amount, 2)` — float add and banker's
  rounding in a file the pre-commit hook does not cover. Inputs are
  cent-quantised so drift is unlikely; fix for the rule, `_q2(_money(...))`.
- **N7 — Low.** Unlocked read-modify-write on
  `ride_payment_sources.allowance_debit_amount` (`payment_service.py:741-750`,
  late corporate tip attribution, single writer today).
- **N8 — Info. Unkeyed Stripe mutations that create no charges:**
  `routes/admin/rides.py:1978` `Customer.create` (orphan customer on retry);
  `routes/drivers/subscriptions.py:229` `Subscription.delete`, `:546,573`
  `checkout.Session.create`; `routes/admin/rides.py:2046,2152,2183` `Invoice.delete`,
  `:2053,2165` `finalize_invoice`, `:2179` `void_invoice`; `routes/payments.py:1050`
  `PaymentMethod.attach`, `:1233` `detach`, `:395,1073,1141,1247` `Customer.modify`.
  Time-bucketed keys without the amount (`routes/wallet.py:173`
  `wallet-topup-{user}-{minute}`, `routes/payments.py:480,1341`) mean two
  different amounts within 60 s produce a Stripe idempotency 400 — a denial,
  not a duplicate. A lint rule (§4) closes the class.

### 1.11 Dispatch and state-machine findings (deep dive)

The good news first: **every status write is a compare-and-swap** with a
status (and usually `driver_id`) predicate that returns 409 on a miss, and the
rider-cancel path claims the row *before* charging the fee
(`routes/rides/cancellation.py:85-101`). No status TOCTOU was found on any
rider/driver/admin transition. The single-offer timeout-vs-accept race is
correctly exclusive (`matching.py:1540-1542` vs `ride_flow.py:319-323` both
predicate on `status=driver_assigned, driver_id=<this driver>`; the loser
logs "lost the race" at `:1552-1555`). Scheduled dispatch trusts a DB CAS
(`utils/scheduled_rides.py:447-452`) rather than its fail-open Redis lock
(`:709-713`), and `scheduled_time` validation rejects DST-gap and ambiguous
local times (`schemas.py:926-951`). `is_available ⇒ is_online` holds because
`set_driver_available()` (`repositories/driver_repo.py:17-18`) is the only
writer of `True` and clamps on `is_online`.

The CLAUDE.md-mandated guard `_require_ride_in_state()` has **zero callers**
for the driver variant (`routes/drivers/_shared.py:651`, re-exported at
`drivers/__init__.py:124,385`) and one for the rider variant
(`cancellation.py:80`). The mandate is met in substance by the CAS pattern, not
in letter; the doc should say so (§5.9).

What is wrong:

- **D1 — Major. Batch-offer accept can race the offer-expiry reaper and penalise
  or offline the winning driver.** `ride_flow.py:291-297` pre-reads the
  `ride_offers` row (`status: pending`), then `:329` CASes the **ride**
  (`{"id", "status": SEARCHING, "driver_id": None}`) independently of the offer
  row; the offer flip at `:411-419` is used only for a metric (`:425-431`). The
  reaper claims only the **offer** row (`matching.py:1704-1709`) and then runs
  `increment_miss_streak`, `update_acceptance_rate(accepted=False)`
  (`:1714-1715`) and either force-offline + Period 0 (`:1728-1739`) or
  `set_driver_available(True)` + Period 1 (`:1746-1748`) — on a driver who now
  holds a `driver_accepted` ride. Result: a driver offline and in Period 0
  mid-trip, or `is_available=True` on an active ride (`set_driver_available`
  never checks for an active ride). Fix: treat 0 rows from the offer flip as
  race-lost and revert the ride CAS, or make `process_expired_offer` re-check
  `rides.driver_id == driver_id` before penalising.
- **D2 — Medium. Ride events carry a `version` only on accept.**
  `socket_manager.py:505-515` accepts a `version` so clients can drop stale
  events; only `ride_flow.py:544-551` passes it. Arrive (`:846`), verify-otp
  (`:907`), start (`:974`), complete (`ride_complete.py:891`), rider cancel
  (`cancellation.py:572`) and driver cancel (`ride_cancel.py:251`) omit it. The
  pub/sub `seq` (`ws_pubsub.py:189`) is per *client*, not per ride, so it
  cannot order ride events. This is the backend half of the mobile regression
  in §2.1 (M2). Fix: pass `version=<row>["version"]` from each CAS's returned
  row.
- **D3 — Low. Period-1 gating is inconsistent.** `ride_complete.py:838-841` and
  `ride_cancel.py:218-228` write Period 1 without inspecting the release
  result; `cancellation.py:468`, `admin/rides.py:737,920` and
  `matching.py:1594-1602` gate on `released.get("is_available")`. A driver who
  went offline before completion gets a phantom Period 1 row. Apply the same
  gate.
- **D4 — Low. Admin complete from `driver_arrived` writes no Period 3.**
  `admin/rides.py:835` allows completion from `("driver_arrived", "in_progress")`;
  the only Period-3 writers are `ride_flow.py:892,960` and `lifecycle.py:111`.
  The regulatory audit then shows a completed trip with no passenger-aboard
  period. Restrict to `in_progress` or write Period 3 first.
- **D5 — Low. `set_driver_available(True)` is read-then-write** (`driver_repo.py:13-27`:
  `select("total_rides, is_online")` then `update().eq("id")` with no
  `is_online` predicate). A go-offline between the two statements yields
  `is_available=True, is_online=False`. Add `.eq("is_online", True)` when
  setting `True`.
- **D6 — Info. Access-token `exp` is not re-checked mid-connection.** The
  heartbeat re-validates revocation (`websocket.py:376-380`, every 10 s) and
  session revocation (`:758-771`, every 30 s) but not `exp` (`:651` "access
  token usable until its exp"). A 15-minute token keeps a socket alive
  indefinitely unless revoked.
- **Backpressure.** No per-connection outbound queue; sends are awaited with a
  2 s timeout and dropped on timeout (`socket_manager.py:396-405`, "heartbeat
  will reap the socket"). Durable messages also land in a Redis ring of the
  last 50 per client for 5 minutes (`ws_pubsub.py:209-211`). Adequate at this
  scale; a bounded drop-oldest queue for location frames is the next step.

**Transition matrix (verified):**

| Transition | Atomic guard | WS rider | WS driver | Period row |
|---|---|---|---|---|
| accept → `driver_accepted` | `ride_flow.py:319-329` | yes + `version` | none (by design, `:502-503`) | 2 (no-op safety net) |
| arrive | `:802-818` | yes | none | stays 2 |
| verify-otp / start → `in_progress` | `:877-889` / `:945-957`, `lifecycle.py:93-104` | yes | none | 3 |
| complete | `ride_complete.py:651` | yes | none | 1, ungated (D3) |
| rider cancel | `cancellation.py:80-96` | yes | `ride_cancelled` | 1, gated |
| driver cancel | `ride_cancel.py:100-113` | yes | none | 1, ungated (D3) |
| driver no-show | `:342-345` | yes | none | 1, gated |
| admin cancel | `admin/rides.py:644` | yes | yes | 1, gated |
| admin complete | `:857` from `driver_arrived`/`in_progress` | yes | yes | 1, gated; no 3 (D4) |
| offer timeout → `searching` | `matching.py:1540-1542` | not verified | `ride_offer_expired` | 0 or 1, gated |
| scheduled → `searching` | `scheduled_rides.py:447-452` | not verified | — | — |

### What is done well on security (keep it)
Every third-party action is SHA-pinned and no workflow uses
`pull_request_target`; the runtime image is digest-pinned, non-root
(`Dockerfile:33,87`) and installed with `--require-hashes`; admin login strips
the refresh token into an `HttpOnly; SameSite=Strict` cookie with a CSRF
double-submit token and pins the edge region to Montréal
(`admin-dashboard/src/app/api/admin/auth/login/route.ts`); admin TOTP MFA with a
Redis failure lockout (`routes/admin/auth.py:79-98`); rider OTP lockout SEC-008
(`routes/auth.py:179-279`); logout-all watermark enforced at token exchange
(`routes/auth.py:1488`); rate limiter keyed on `CF-Connecting-IP`; the two
`verify_signature=False` decodes (`core/middleware.py:447`,
`utils/rate_limiter.py:124`) are for correlation and bucket keys only; a PII
scrubber shared by Sentry and the loguru sink (`utils/sentry_scrub.py`,
`utils/pii.py`); only 6 log lines mention phone/email/GPS and all are masked;
132 RLS policies with a real-Postgres test tier; HSTS, `X-Frame-Options: DENY`,
`Permissions-Policy` and a nonce CSP on the admin (`next.config.*:29-60`).

---

## 2. 🛡️ Error Handling & Telemetry

### What the user sees vs. what the admin can act on
The backend error envelope is better than most Series-A ride-share codebases:
an `ErrorCode` enum (`utils/error_handling.py:63`), a `SpinrException` hierarchy
with 40+ typed subclasses, one handler per exception family
(`error_handling.py:607-887`), `request_id` bound into loguru context and echoed
in the body, and 5xx detail sanitised unless it is an `ERR_*` sentinel. A rider
never sees a stack trace from the backend. On the admin side
`shared/api/cachedClient.ts:101-156` surfaces `detail` only when it is a string
and falls back to "Request failed"; only two admin pages still toast
`error.message` verbatim (`promotions/page.tsx:437`, `cloud-messaging/page.tsx:351`).

The failure modes that still reach users:
1. **Whole-fleet 503 during a DB breaker window** (§1.8) — the most user-visible
   error path in the system is the health check itself.
2. **Admin 4xx text** (§1.7) — the one place raw exception text is shown to a
   human.
3. **Pickup OTP NULL → 500** (§1.6).
4. **Dispatch fail-open branches** — `matching.py:578` (presence filter failed:
   offer to every DB-online driver), `:593` (offer-skip MGET failed: re-offer a
   driver who just timed out), `:847` (cascade skip failed). These are
   deliberate — the comments explain the C2 cascade they avoid — but only the
   presence branch has a metric (`spinr_dispatch_presence_filter_failed_total`);
   the driver-facing symptom (the same dead offer twice) has none.

### What the admin/backend can monitor
Good: Prometheus registry (`utils/metrics.py`) scraped by Grafana Alloy
(`metrics-agent/`) into Grafana Cloud; per-phase dispatch histogram
`spinr_dispatch_attempt_duration_ms{phase}`; thread-pool and `run_sync`
queue/exec gauges; `capacity_watchdog`; loop watchdog with Slack staleness
alerts; the new insurance-period write-failure alert and Period-2 reconciler
(C55, `379e8b7`); the loguru convention gate now catches `extra=`/`exc_info=`
misuse through re-exports (C65/C69) — 161 offenders fixed in one sweep.

Gaps:
1. **Swallow census is growing.** 1,634 `except Exception` (+78 in four days), 215
   → `warning`, 17 → `pass`. The CLAUDE.md rule ("never warning-and-continue on
   DB/auth/payment/dispatch") is a convention with no gate. Add a Semgrep or
   Ruff-plugin rule scoped to `routes/{rides,payments,wallet,webhooks,auth}` and
   `services/{payment,dispatch,corporate}*` that fails CI on
   `except Exception` followed by `warning`/`pass`/`continue`.
2. **Fail-open needs a budget.** Each deliberate fail-open in dispatch should
   increment a labelled counter and flip to fail-closed after N consecutive Redis
   errors; today an outage of the offer-skip keys is invisible.
3. **PostgREST saturation is invisible to the watchdog.** 64 threads × 2 workers
   × 8 machines = 1,024 in-flight requests into a Supabase-managed PostgREST
   `db-pool` of roughly 10–15 connections; excess queues *inside PostgREST*, so it
   appears as `spinr_db_run_sync_exec_ms` inflation while
   `capacity_watchdog.py:94` watches `queue_depth > 50` and never trips. Alert on
   `exec_ms` P95.
4. **`print()` × 261** in importable modules (import/backfill/diagnose services)
   bypasses context and Sentry.
5. **Two log formats, no log store.** loguru context plus stdlib `logging` per
   module; production sink is stdout (Fly log tail — *not verified*). Route
   stdlib through a loguru intercept, JSON in production, ship to Loki through
   the Alloy agent already deployed.
6. **SLOs are written, not wired.** `docs/slo.md` has targets and alert
   thresholds; `monitoring/synthetic-checks.yaml` says in its own header that
   no vendor consumes it (E4 open). No burn-rate alert exists on any SLA row.
7. **Tracing deferred by ADR-014 (2026-09-04).** Reasonable while one replica
   handles one request. The ADR's own trigger ("a request that legitimately
   spans many internal hops") is met the day §1.3 ships: request → outbox →
   worker → push. Pre-register the re-open with that milestone.
8. **CI "gates" that annotate instead of block.** `ci.yml:102` ruff lint is
   `continue-on-error: true`; admin e2e and mobile e2e are non-blocking on PRs
   (`ci.yml:451,624`); `ci-guardrails.yml` has 17 `continue-on-error` steps
   covering the coverage-regression check, lint-warning trend, breaking-change
   detection and risk label. The repo's own history (a red `main` for 2.5 h
   noticed by nobody because the Slack secret was unset) shows what advisory
   gates are worth. Two concrete instances observed on this review's own PR
   (#5033, one Markdown file):
   - **`maestro-e2e.yml` fails on every push to every branch, with zero jobs.**
     3,103 runs to date; the latest five on `main` and on an unrelated feature
     branch are all `conclusion: failure`, `total_jobs: 0`, and every run is
     named by file path instead of the workflow's declared
     `name: Maestro Mobile E2E …` — GitHub's signature for a workflow file that
     failed validation. Cause: the job-level `if:` at `maestro-e2e.yml:66-74`
     references `matrix.app.dir`, and `matrix` is not an allowed context in
     `jobs.<id>.if`. This is the "already failed before the merge" check C21
     recorded on #3719, it is a second reason B25's `run-maestro` label can
     never fire, and it is a permanent red light that teaches everyone to
     ignore red. Fix: drop the `matrix.app.dir` clause from the job `if` and
     apply it on the first step instead (or build the matrix from
     `inputs.apps` with `fromJSON`).
   - **The risk labeler mislabels any PR whose base has moved.**
     `ci-guardrails.yml:1405-1440` computes changed files with a two-dot
     `git diff BASE_SHA HEAD_SHA`, so a PR inherits everything `main` gained
     after the fork point as its own change. #5033 touches one `.md` and was
     labelled `risk:high` because `main` gained 8 backend files after the
     branch was cut. C14 fixed the identical bug in `migration-check.yml` on
     2026-08-10 and the coverage job in this same file already uses
     `git merge-base`; the sweep never reached this step. The label drives the
     Tier 5–7 template expansion and the `risk:high` pre-merge checklist, so it
     misdirects reviewer attention in both directions. Fix: diff from
     `$(git merge-base BASE_SHA HEAD_SHA)`.
9. **Mobile telemetry** — see §2.1.

### 2.1 Mobile error handling and telemetry (deep dive)

| Metric | rider-app | driver-app | shared |
|---|---|---|---|
| Screens (`app/**/*.tsx`, excl. `_layout`) | 46 | 37 | — |
| Test files | 134 | 134 | 13 |
| `: any` / `as any` (excl. tests, `.d.ts`) | 323 | 306 | 21 |
| `console.log(` in `app/`, `hooks/`, `store/` | 44 | 45 | 52 |
| Empty `catch` blocks | 18 | 15 | 10 |
| Largest files | `ride-options.tsx` 2,281; `store/rideStore.ts` 1,326; `ride-completed.tsx` 1,284 | `hooks/useDriverDashboard.ts` 1,955; `(tabs)/index.tsx` 1,755; `(tabs)/profile.tsx` 1,597 | `api/client.ts` 1,261; `SupportScreen.tsx` 917; `store/authStore.ts` 756 |

**Done well.** Sentry is PIPEDA-hardened (`shared/services/errorReporting.ts:48-69`:
`sendDefaultPii: false`, no screenshots or view hierarchy, `tracesSampleRate: 0.2`,
console breadcrumbs dropped, user reduced to `{ id }`). Error surfacing is
centralised: 189 call sites go through `getApiErrorMessage` (`shared/api/client.ts`)
and zero `Alert.alert`/`Toast.show` lines pass a raw `error.message`. The
background-location task self-heals on sign-out (`driver-app/utils/backgroundLocation.ts:250-255`)
and logout teardown purges tokens, the SQLite outbox and the last location
(`utils/sessionTeardown.ts:41-78`). A server-authoritative monotonic ride
`version` guard already exists (`rider-app/store/rideStore.ts:1223-1229`).

**Verified from prior plans.** T6 confirmed: `LogRocket.init('gfuign/spinr')`
with **no options object** in either app (`rider-app/app/_layout.tsx:320`,
`driver-app/app/_layout.tsx:372`) — no network/text/input sanitiser, no
`redactionTags`; capture is paused only on `safety-hub`, `manage-cards`,
`payment-confirm`, `stripe-onboarding`, `documents`; no ride-tracking screen
pauses it; iOS is on by default (`_layout.tsx:197`). T7 confirmed: the driver's
`activeRide` is a persisted React Query key (`shared/hooks/queries/driverQueries.ts:106-113`);
the rider's is a manual `AsyncStorage.setItem` (`rider-app/store/rideStore.ts:44`).
The Firebase web SDK is imported once (`shared/config/firebaseConfig.ts:1`) and
consumed by nothing but jest mocks.

**New findings.**
- **M1 — Medium (PIPEDA). React Query persists every successful query to
  unencrypted AsyncStorage and nothing clears it on logout.**
  `shared/api/queryClient.ts:76-84` creates the persister; no
  `dehydrateOptions`/`shouldDehydrateQuery` exists anywhere;
  `PersistQueryClientProvider` gets only `persister`, `maxAge: 24h`, `buster`
  (`rider-app/app/_layout.tsx:841-846`, `driver-app/app/_layout.tsx:592-597`);
  zero hits for `queryClient.clear`/`removeClient`. On a shared device the
  previous driver's profile, earnings, active ride (pickup/dropoff, rider name)
  and notifications rehydrate for the next launch for up to 24 h. Fix: an
  allow-list `shouldDehydrateQuery`, and `removeClient()` + `queryClient.clear()`
  in the logout callback.
- **M2 — Medium (UX correctness). Ride status can regress.** The version guard
  applies only when an event carries a numeric `version` (`rideStore.ts:1219-1227`);
  only `ride_status_changed` forwards one (`useRiderSocket.ts:181-185`) while
  `driver_accepted`/`driver_arrived`/`ride_started`/`ride_completed` (`:102-131`)
  apply unversioned, and `fetchRide` (`:831`)/`fetchActiveRide` (`:484`) overwrite
  with only an id check. Scenario: `driver_accepted` triggers `fetchRide`; the
  slow GET lands after `ride_started`, and the rider's screen drops back to
  "driver accepted" until the next 15 s poll. Backend half is D2 in §1.11. Fix:
  stamp `version` on every ride event and REST body; apply the same drop in
  both fetchers.
- **M3 — Medium (regulatory communication). Background location has a
  signed-out gate but no offline gate.** `backgroundLocation.ts:374-389` runs
  with `AutomotiveNavigation`, `pausesUpdatesAutomatically: false` and a
  foreground notification "You're online and receiving ride requests";
  go-offline calls `stopBackgroundLocation()` (`useDriverDashboard.ts:1654`), but
  a thrown `stopLocationUpdatesAsync` is downgraded to `recordNonFatal`
  (`:583-589`) and the task's only self-check is `isSessionEnded()` (`:250`).
  A signed-in, offline driver then keeps uploading GPS in Period 0 with a
  notification that says they are online. `CAR_LOCATION_TASK`
  (`lib/androidAuto/carLocationTask.ts:311,340`) has the same shape. Fix:
  persist a `driver_online` flag the task reads next to `isSessionEnded()` and
  self-stops on; treat `stop_updates_failed` as a retry.
- **M4 — Low (consent). Meta SDK initialises at module load** (`rider-app/app/_layout.tsx:165`,
  `driver-app/app/_layout.tsx:128`) with advertiser tracking correctly off
  (`shared/analytics/meta.ts:65-66`) but `setAutoLogAppEventsEnabled(true)`
  (`:74`) sends install/activation events before any consent screen; no
  ATT/consent gate found. Defer init until the stored consent version is
  accepted.
- **M5 — Low (OTA safety).** `runtimeVersion` is a hand-bumped literal
  (`rider-app/app.config.ts:27` `'2.1.0'`, driver `'2.7.0'`) under a comment that
  says OTA risk is "zero" pre-launch — false during live testing. No
  `fallbackToCacheTimeout`/`checkAutomatically` set. `driver-app/eas.json:41-45`:
  the `android-auto` profile is `distribution: store` on `channel: preview`, so a
  store binary receives preview OTAs. Add a CI check that fails when native
  deps change without a `runtimeVersion` bump; give `android-auto` its own
  channel.
- **M6 — Low.** Dead `firebase` web SDK in both `package.json` files.
- **M7 — Low.** Two error-surfacing escape hatches (`rider-app/app/ride-status.tsx:659-661`
  toasts `response?.data?.detail || err.message`; `driver-app/app/documents.tsx:191`
  raw DocumentPicker message); 43 empty catches; 141 `console.log`s in
  production paths. `rider-app/app/driver-arriving.tsx:763` posts to a driver
  endpoint from the rider app, gated by `__DEV__`.

---

## 3. 🐢 Performance Bottlenecks & Optimizations

**The only real numbers on record** (`docs/audit/2026-09-02-t16-staging-load-test-results.md`,
Scenario A, 60 bots, smallest Fly tier, pre-C50 build):

| Path | SLA (CLAUDE.md) | Observed | Verdict |
|---|---|---|---|
| Fare estimate P95 | < 300 ms | **910 ms** (n=100, 0 failures) | breached, independent of the harness artefact |
| Dispatch offer→accept P95 | < 2 s | 594 ms (n=1) | not meaningful |
| `/health` DB ping | — | 30–67 ms | DB was not the bottleneck |

No production P95 exists anywhere in the repo. The harness had never been run
before 2026-09-02 and needed four fixes to produce a number.

Ordered by SLA impact:

| # | Path | Bottleneck (evidence) | Cost | Fix |
|---|---|---|---|---|
| P1 | Fare estimate | A fresh `httpx.AsyncClient` per Google call (`routes/rides/_shared.py:132`, `utils/maps_eta.py:106,216,320`, `utils/route_distance.py`) = TCP+TLS handshake each time; `get_service_area_for_point` RPC per point — pickup, dropoff and every stop (`estimates.py:174,222,240`) after `service_areas` was already loaded at `:169`; `service_areas`/`vehicle_types` uncached, re-read per estimate | 5–7 sequential RTTs ≈ 150–250 ms floor before Directions; explains most of the 910 ms | One module-level client opened in lifespan (pattern already in `apns_client.py:167`); test loaded polygons in-process, RPC only when the polygon column is empty; Redis cache 30–60 s with invalidation from admin writes; OSRM (`deploy/osrm` exists, unused) for the estimate, Google for the locked quote |
| P2 | Dispatch offer → notification | Default path ≈ 10–14 sequential PostgREST RTTs; claim (`matching.py:1131-1145`, Redis DEL + UPDATE + DEL per driver) and insurance writes (`:1213`) run per driver sequentially; the one-transaction RPC (`dispatch_claim_batch`, migration 402) is built and dark | ≈ 200–300 ms for 3 offers on top of the reads | Enable `dispatch_direct_pool_enabled` after staging parity and a Scenario A/B run; `gather` the insurance writes meanwhile. ETA ranking is correctly bounded at 1.2 s (`matching.py:952`) |
| P3 | Driver location write | `location.py:230,278,583` re-read `drivers` by `user_id` uncached per batch although a 30 s cached helper exists (`_base.py:518`); marker write gate `location_marker_write_gate_enabled` defaults OFF (`location_write_gate.py:132`) so REST writes land every flush | ≈ 60–110 ms of a 150 ms budget | Use the cached helper; flip the gate once shadow metrics are read |
| P4 | Background load | 11 loops tick with no jitter (`route_finalizer.py:566`, `route_gap_monitor.py:370`, `push_retry.py:97`, `support_sla.py:204`, `safety_checkin_loop.py:85`, `period1_distance_finalizer.py:173`, `insurance_period_reconciler.py:279`, `driver_daily_rollup.py:345`, `stale_p3_closer.py:297`, `zoho_desk_sync.py:231`, `referral_payout.py:265`) → all 16 processes tick at t0 after a rolling deploy; `preauth_capture.py:199` TTL = 2× interval halves its own cadence; `safety_checkin` (30 s) reads in-progress rides on every process (32 reads/min fleet-wide), `subscription_expiry` (N+1, no lock), `support_sla`, `corporate_autotopup` (no lock); `driver_claim_reaper.py:80-115` 2 reads per driver | DB load that scales with replicas, not riders | `asyncio.sleep(random.uniform(0, interval))` before each loop (pattern at `stuck_ride_sweeper.py:145`); TTL = `interval × 0.85`; leader lock on each; batch the reaper with `$in`. All of it becomes moot once §1.3 runs loops on one worker |
| P5 | Thread pool | `_DB_THREAD_POOL_SIZE = 64` (`_base.py:161`) × 2 workers × 8 machines vs a ~10–15 connection PostgREST pool | queues inside PostgREST, invisible (§2 gap 3) | Size `DB_THREAD_POOL_SIZE ≈ 2 × db-pool / workers`; confirm pooler facts (C50 plan G5) |
| P6 | Admin residuals | `admin/drivers.py:2292 limit: int = 100` and `admin/rides.py:3481 limit: int = 50` without `le=` (PostgREST silently caps at 1,000 PII rows); `/drivers/stats` 5,000 rows (`:864`), `/drivers/expiring` 5,000 + 10,000 docs (`:1441,1489`), referral leaderboard 10,000 users + 50,000 rides (`:2733,2755`), `/earnings/rides` `le=10000` (`admin/rides.py:2495`) | seconds-long admin pages | `Query(le=500)`; RPC aggregates as already done for payouts |
| P7 | Geo provider | Live default is almost certainly `legacy` (DB default from migration 397 overrides `schemas.py:707`); PostGIS path costs 2 RTTs (RPC + `get_rows` by ids) vs 1; H3 can never warm (no writer) | the spatial index paid for since migration 170 is still unused | One admin action: set the row to `postgis`; add `upsert_driver` to the location write path behind the existing flag |

Fixed since 09-01 and worth crediting: admin WS fan-out throttled (P4 old);
admin list pagination and RPC stats; the three sync calls that blocked the
event loop (`earnings.py:118,416`, `ride_reads.py:85,194`); `_DEFAULT_ROW_LIMIT`
and the `limit=0` bug; `driver_documents` indexes (migration 368); httpx
keep-alive sized to the pool (`supabase_client.py:46-50`); `route_gap_monitor`
and `document_expiry` batched.

**Uber/Lyft comparison.** Both moved supply lookup out of the database into an
in-memory geo index (H3 at Uber), made the trip service the single writer of
trip state, and run ETAs on their own routing stack with the third party as
fallback. Spinr has each of those three pieces *in the repository* — the H3
index, the one-transaction claim RPC, the OSRM deployment — and every one is
dark. The performance plan here is mostly "bring staging to parity, load-test,
then turn on what is already built", which is a far better position than
needing to build it.

---

## 4. 💡 Tech Stack & Architecture Recommendations

| Layer | Current | Verdict | Recommendation | Why |
|---|---|---|---|---|
| API framework | FastAPI 0.141 / Pydantic 2 / uvicorn (2 workers) | Keep | — | Right choice; the problems are around it |
| DB access | supabase-py (PostgREST) on a 64-thread pool; psycopg3 pool to Supavisor for the claim batch (dark) | **Extend the pool, keep PostgREST for CRUD** | After C50 validates, move ride state transitions, settlement, and the location marker write onto `dispatch_pool`-style transactions; write `rides` + `drivers` + `driver_insurance_periods` in one transaction | Reconcilers (`stuck_ride_sweeper`, `stale_p3_closer`, `orphaned_hold`, `stale_intent`) exist because multi-row invariants are not transactional. psycopg3 was the right pick over asyncpg for Supavisor transaction mode |
| Geo | Bounding box + haversine live; PostGIS KNN and H3 providers built | **Flip** | PostGIS now; H3 + Redis `GEOSEARCH` once a writer exists; skip S2/H3 meshes beyond that | Province-scale, not planet-scale |
| Background work | 41 asyncio loops in the API process; `worker.py` coded | **Deploy** | Fly `[processes]` worker group → then arq (Redis-backed, leases, cron) one job per loop body; Temporal only if a payout/settlement saga proves it needs durable workflow state — decide by ADR | Isolation and one leader by construction |
| Leader election | `SET NX` fail-open | Replace | Strict variant for money loops; jitter everywhere | Fail-open on money is the wrong default |
| Realtime | In-process registry + Redis pub/sub with per-channel `seq` replay; 10 s heartbeat; 30 msg/s; 64 KB | Keep, harden | Stamp `broadcast_ride_status` with a per-ride monotonic `seq` (Redis `INCR ride:{id}:seq`) so clients detect gaps mid-connection, not only on reconnect; bounded outbound queue with drop-oldest for location frames | Missed-event recovery today is "refetch on reconnect" |
| Routing / ETA | Google Directions + Distance Matrix inline; `deploy/osrm` unused | **Add** | OSRM/Valhalla for estimates and ranking; Google for the quote-locked confirm | 910 ms P95 and the Maps bill |
| Cache | Redis for presence, locks, fares (300 s) | Add | `service_areas`, `vehicle_types`, area fees, `app_settings` in Redis (30–60 s) with pub/sub invalidation from admin writes | Read per estimate today |
| Feature flags | `app_settings` booleans, 60 s in-process TTL, ADR-011 read-failure semantics | Add a layer, not a vendor | A typed `flags.py` (`Flag(name, default, fail_mode, rollout)`) with percentage/company targeting and an audit row on every flip; migrate the ~25 `*_enabled` keys | Cannot canary a money-path change to 5 % of rides; an SaaS flag service is more surface than a 3-person team should run |
| Secrets | Stripe/Twilio/Maps keys in the `settings` row | Reconsider | Fly secrets as source of truth + `POST /internal/reload-secrets` (super_admin, audited); admin UI writes through | A DB read leak should not yield the Stripe key; rotation without deploy is preserved |
| Tracing | `X-Request-ID`; OTel deferred (ADR-014) | Defer, with a trigger | Re-open when §1.3 ships; `opentelemetry-instrumentation-{fastapi,httpx,redis,psycopg}` → Grafana Tempo via the existing Alloy | The ADR's own condition |
| Logs | loguru + stdlib, stdout | Add | Intercept handler, JSON, Loki via Alloy, 30-day retention | One format, searchable, retained |
| Contract | Hand-written TS types in `shared/types`; 1,102 `any` | **Add** | Export `openapi.json` in CI, generate `shared/build-types` with `openapi-typescript`, fail the PR on drift | `any` count falls mechanically; mobile/admin error handling can be checked against every `ErrorCode` |
| Payments | Stripe + Connect; idempotent payouts/top-ups; ledger; outbox for receipts | Keep | Idempotency-key lint on every `stripe.*.create/modify`; DLQ table for failed webhook handlers instead of unclaim-and-wait; deploy the outbox consumer (§1.3) | Already strong (see §1.10) |
| Mobile | Expo 57 / RN 0.86 / React 19, EAS, OTA | Keep, diet | Keep Sentry + Crashlytics; remove LogRocket, `expo-insights`, `expo-observe`, the unused Firebase web SDK; gate the Meta SDK on consent; crash-free-rate gate before OTA promotion | Five telemetry SDKs are five PII surfaces |
| Admin | Next 16 / React 19 / Tailwind / zustand / zod / recharts; Storybook; Playwright + axe; visual regression now blocking | Keep | `jose.jwtVerify` in middleware; React Query with cursor pagination; `--max-warnings 0` on changed files via `lint-staged` | The 1,751 budget is a bankruptcy declaration |
| Hosting | Fly `yyz` primary (8 machines, suspend-autoscale), Railway standby (stale), Cloudflare CNAME failover | Keep, fix | Restore or retire the standby; migrations in release; canary/bluegreen; Cloudflare health-based LB if two hosts stay | A stale standby is worse than none |
| CI/CD | 41 workflows; Postgres service; RLS + direct-pool tiers; bandit/Trivy/ZAP/Gitleaks; Dependabot auto-merge; migration-check; visual regression | Keep, **re-order** | Deploy waits for CI (§1.1); lint and coverage regression become blocking; `pytest-xdist` to keep the 20-min job inside budget; OpenAPI drift gate; Maestro on label + nightly | Gates exist; ordering and enforcement are the gap |
| AI assistant | Anthropic + OpenAI + Gemini SDKs + `mcp` | Trim | One provider SDK behind `ai/providers/`; keep the ScrubPolicy egress boundary (ADR-012) | Three SDKs is audit burden and attack surface |
| Packaging | `python -m backend.server` vs top-level; 1,020 dual-import blocks growing 9/day; `db.py` shim (19 importers); `schemas.py` shadowed by a `schemas/` package that re-executes the flat file under another module name | **Fix** | `backend/pyproject.toml`, one import root, one mechanical PR removing every dual-import block and both shims; then `import-linter` contracts (`routes` → `services` → `repositories`) | The CLAUDE.md rule that protects the pattern exists only because packaging was never done |

**Missing tools worth adding (all cheap):** Semgrep house rules (`detail=str(e)`,
float in fare files, router mounted without `require_module`, PII names in log
format strings) as a blocking job; `hypothesis` for the ride state machine;
`mutmut` quarterly on `fare_service.py`/`utils/money.py`; `pytest-xdist`;
`import-linter`; `openapi-typescript`; `arq`; OSRM in the estimate path; Grafana
alert rules generated from `docs/slo.md`.

### How the leaders do it, and what actually transfers

| Concern | Uber / Lyft pattern | Spinr today | What transfers at 1–2 cities |
|---|---|---|---|
| Supply index | In-memory hex index (H3), supply service separate from trip service, DB written asynchronously | Row scan → Python; H3/Redis index dark | PostGIS KNN now, Redis GEO next; no service split |
| Trip lifecycle | One writer, event-sourced, idempotent commands, sequence numbers on push | Status column + optimistic filters + four reconcilers | Transactional transitions on the pool; per-ride `seq`; reconcilers demoted to detectors that page when they repair |
| ETA | Own routing + ML | Google inline, 3.5 s worst case accepted | OSRM for estimates |
| Surge | Hex-cell demand/supply, capped, shown pre-booking | Area tiers, 2.5× hard cap, pre-booking, corporate exempt | **At parity for the market.** Leave it |
| Background work | Dedicated workers, durable workflows (Cadence/Temporal), leases | Loops in the API process, fail-open lock | Worker group + arq; Temporal only by ADR |
| Flags | Typed flag service, % rollout, kill switches, audit | `app_settings` booleans | Typed layer, not a vendor |
| Observability | Tracing, metrics, logs, SLO burn-rate paging | Metrics live; logs stdout; tracing deferred; SLOs unwired | Burn-rate alerts + Loki now; OTel at the worker milestone |
| Schema change | Automated, gated, expand/contract | Human + hand-exported DSN | `release_command` |
| Mobile | One crash stack, generated clients, staged OTA | Five SDKs, hand types, OTA | SDK diet + OpenAPI types |
| Merge/deploy | Mandatory review, green-only merge, canary, auto-rollback | Two collaborators, auto-merge before CI, deploy on push | **This is the gap that matters most** |

---

## 5. 🛠️ Maintainability & Code Smells

1. **God files keep growing.** `routes/admin/drivers.py` 4,334 (+24 in four days),
   `routes/admin/rides.py` 4,171 (+272), `services/payment_service.py` 2,267,
   `routes/webhooks.py` 2,266, `routes/auth.py` 2,062, `routes/drivers/subscriptions.py`
   1,994, `routes/rides/matching.py` 1,938, `backend/features.py` 1,919 (a
   grab-bag router at package root: support tickets, FAQs, surge, scheduled
   rides, multi-stop, safety, push). 15 backend files exceed 800 lines. The
   admin dashboard went the other way this week (`drivers`, `service-areas`,
   `earnings` pages decomposed, #4952/#4953/#4955) — the same discipline is
   needed on the backend.
2. **Three compatibility shims at the package root.** `db.py` re-exposes a module
   removed in PR #290 and is still imported by 19 non-test modules;
   `schemas/__init__.py` loads `schemas.py` through `importlib` under the name
   `_schemas_flat` and injects its names (classes therefore report a different
   `__module__` than their import path); `dependencies/` vs the `dependencies.py`
   that `ARCHITECTURE.md` documents. Each is a small tax on every new engineer.
3. **Dual-import blocks: 1,020, +36 in four days.** The rule that protects them is
   producing debt at nine blocks a day. Packaging (§4) retires the rule.
4. **Routers mounted at 2–3 prefixes each** (`server.py:364-447`: `/api/v1`, `/api`,
   `/api/portal`, bare). Every duplicate mount doubles the rate-limit key space,
   the audit surface and the OpenAPI document. Deprecate with a 410 sunset
   header and a date.
5. **Migrations.** 66 duplicated numeric prefixes (up from ~60), 35 files still
   declare `FOR ALL` RLS policies, and file name is the idempotency key, so
   history can never be tidied. Accept it, but make CHECK B's cross-PR gap a
   merge-queue rule rather than a nightly report.
6. **Lint debt is frozen, not shrinking.** `--max-warnings 1751` unchanged;
   `any` × 1,102; ruff non-blocking in CI. A `lint-staged` pre-commit with
   `--max-warnings 0` on staged files makes the number monotone.
7. **Legacy import code in the production import graph.** 15 import/backfill/
   correction services, `diagnose_*.py`, `list_users.py`, `seed_vehicle_types.py`,
   261 `print()`s. Move under `backend/scripts/legacy/` after the Oct 31
   decommission and out of the mounted routers.
8. **Repo root.** Three `.docx`, one `.csv`, one ad-hoc `.sql`, a deprecated
   `frontend/` tree, `plans/`, `reports/`, `test_reports/`, and
   `SPINR_CODE_REVIEW.md` (whose 16 criticals are fixed but which still reads as
   open). Move review artefacts to `docs/audit/`, delete the rest.
9. **Docs drift.** `ARCHITECTURE.md` still says Expo SDK 54, Railway hosting,
   `routes/rides.py` (now a package), "16 background loops" (`fly.toml:12`)
   vs 41. `CLAUDE.md` is ~600 lines of incident narrative: split rules from
   history. The state-machine guard it mandates (`_require_ride_in_state()`)
   has one caller in `routes/` (`cancellation.py:80`, the rider variant); every
   other transition is guarded by a status-filtered atomic update — which is
   fine, and the doc should say so.
10. **Documentation is otherwise a genuine strength**: 14 ADRs, 60+ runbooks,
    an SLO doc, a threat model, a change-impact template. The gap is that
    process documentation exceeds process enforcement.

---

## 6. 🧪 Testing & QA (Missing Edge Cases)

**What exists** is above average for the stage: 12,925 backend tests in 814
files; 7 `test_e2e_*.py`; real-Postgres tiers for RLS and the direct pool wired
into `ci.yml` with `TEST_DATABASE_URL`; admin Playwright (29 specs) + axe +
visual regression now merge-blocking; Jest on both apps; bandit/Trivy/ZAP/
Gitleaks; a migration safety check; a loguru-convention gate.

**Quality findings**
1. **The mock fixture cannot fail a race.** `tests/conftest.py:179`
   `mock_supabase_client` is a `MagicMock` chain whose `execute()` returns seeded
   data; it never applies filters, limits or the status predicate of an atomic
   update. Any unit test of "0 rows → 409" proves only that the code handles a
   seeded `[]`. Race semantics are real only in `tests/direct_pool` and
   `tests/rls`. Extend that tier to `wallet_apply_delta`, settlement and the
   PostgREST claim path.
2. **Hollow assertions.** 226 test lines whose only check is `is not None`; 6
   `assert True`; `test_sgi_template_versions.py` has 3 tests and 0 asserts.
3. **No property-based or mutation testing** (`hypothesis`, `mutmut` absent);
   `test_ride_state_machine.py` still has 14 tests for a machine with 7 states
   and ~12 transitions.
4. **Coverage gate 60 %** against a plan of 70 by July; per-package floors in
   CLAUDE.md (90 % payments/fare/crypto, 80 % rides/dispatch/corporate) are
   not enforced as separate steps; the coverage-regression job is
   `continue-on-error`.
5. **No `pytest-xdist`** on a 20-minute job; the suite will hit the timeout
   before it hits 80 % coverage.
6. **Load testing** ran once, on a stale staging, with n=1 for the dispatch
   SLA. Not in any workflow; no stored baseline to compare against.
7. **Maestro** still `workflow_dispatch`/label only; no iOS lane (B25).

**Edge-case coverage by name** (grep over `backend/tests`): double-accept 2
files; offer-timeout-vs-accept 1; tip-after-payout **0**; allowance-exhausted 1;
quote/estimate expiry 1; refresh-token reuse 3; dispute-after-refund 7; GPS
plausibility 31; WS token expiry 10.

### Missing edge-case matrix (prioritised by blast radius)

| # | Area | Scenario | Invariant protected | Belongs in | Existing partial |
|---|---|---|---|---|---|
| 1 | Money | Tip added after the driver's weekly payout already transferred | Lands in the next payout; `/rate` rejects post-settlement tips | `test_tips_after_payout.py` | covered by design (`rating.py:69-73`, `earnings.py:79`), no named test |
| 1b | Money | `payment_intent.payment_failed` redelivered after a later PI settled the ride (§1.10 N1) | A paid ride never regresses to `failed`; one charge | `test_webhook_ordering.py` | none |
| 1c | Money | Driver marks no-show on a card-paying rider (§1.10 N2) | Fee captured from hold or fresh charge; driver credit only after collection | `test_no_show_fee_card.py` | 7 no-show files, wallet only |
| 1d | Money | Stop added mid-trip on a promo ride (§1.10 N3) | `grand_total` = fare + fees + tax − discount; receipt lines equal the charge | `test_stops_repricing.py` | none |
| 1e | Money | Admin replays a stuck `charge.dispute.closed` (§1.10 N4) | One ledger row per balance transaction | `test_dispute_ledger_replay.py` | none |
| 2 | Money | `payment_intent.succeeded` arrives before the ride row is `completed` | Settlement ordering; no double capture | `test_webhook_ordering.py` | `test_webhook*` (78 files mention replay) |
| 3 | Money | Duplicate delivery after `unclaim_stripe_event` on handler failure, for every handler | Replay safety per handler, not per claim | `test_webhook_replay.py` | partial |
| 4 | Money | Corporate allowance exhausted between estimate and settlement | Fallback order master wallet → rider card, one charge | `test_corporate_settlement_fallback.py` | 1 file |
| 5 | Money | Surge changes between estimate and confirm with an expired estimate token | Quote lock honoured or re-quote forced, never silent re-price | `test_estimate_token.py` | 1 file |
| 6 | Money | Partial refund then `charge.dispute.created` on the same charge | Double-entry ledger, dispute fee | `test_dispute_after_refund.py` | 7 files |
| 7 | Money | GST/PST line rounding: 3 stops, promo, tip — per-line `ROUND_HALF_UP` sums equal `grand_total` | Receipt line items reconcile | `test_receipt_rounding.py` | — |
| 8 | Dispatch | Two drivers accept the same offer within 100 ms (real Postgres) | Exactly one 200, one 409 + `ride_taken` | `tests/direct_pool/test_accept_race.py` | 2 files (mocked) |
| 9 | Dispatch | Offer expires at T, driver accepts at T+50 ms — single-offer AND batch (`ride_offers`) paths (§1.11 D1) | Reaper revert vs accept are mutually exclusive; the winner is never penalised or offlined | `test_offer_timeout_race.py` | 1 file, single-offer only |
| 9b | Dispatch | Admin completes a ride from `driver_arrived` (§1.11 D4) | Period 3 row exists for every completed trip | `test_insurance_periods_admin.py` | none |
| 9c | Dispatch | Go-offline lands between `set_driver_available`'s read and write (§1.11 D5) | `is_available ⇒ is_online` | `tests/direct_pool/test_driver_available_race.py` | none |
| 10 | Dispatch | Redis skip-key MGET fails → same driver re-offered | Fail-open is counted and bounded | `test_dispatch_redis_degraded.py` | none for skip keys |
| 11 | Dispatch | Presence empty because Redis is down; DB says 40 drivers online | Fail-open metric increments; no offer storm | same file | metric exists, no test |
| 12 | Dispatch | Scheduled ride booked from a DST zone for a Saskatchewan pickup | Dispatch fires at local pickup time | `test_scheduled_rides_tz.py` | 167 files mention tz (generic) |
| 13 | Dispatch | Two replicas run `scheduled_dispatcher` in the same second | Atomic claim; one dispatch | same file | — |
| 14 | Lifecycle | Rider cancels while `driver_arrived` and the no-show clock has started | Fee, Period 2→1, hold release all land or none | `test_ride_state_machine.py` | 7 no-show files |
| 15 | Lifecycle | Driver app killed mid-`in_progress`; `stale_p3_closer` vs driver resume | One writer wins; Period 3 closes once | `test_stale_p3_vs_resume.py` | 6 files |
| 16 | Lifecycle | Pickup OTP: NULL stored, empty submitted, 6th wrong attempt | 409/400, never 500; lockout | `test_pickup_otp.py` | none |
| 17 | Insurance | Admin direct-assign then driver goes offline before accept | Period 2 row closed, Period 0 opened | `test_insurance_periods_admin.py` | C66 tests |
| 18 | Insurance | `driver_insurance_periods` write fails during accept | Alert fires; reconciler repairs; ride still proceeds | `test_insurance_write_failure.py` | C55 |
| 19 | Auth | Replayed old refresh token after rotation | Whole family revoked; audit row | `test_refresh_reuse.py` | 3 files |
| 20 | Auth | WS connection outlives access-token expiry by 20 min | Server-side re-validation closes it | `test_ws_token_expiry.py` | 10 files |
| 21 | Auth | Admin JWT with a forged signature hits the Next middleware | Redirect to `/login` | `admin-dashboard/src/__tests__/middleware.test.ts` | none |
| 22 | Location | GPS jump > 200 km/h; client timestamp in the future; batch with one bad point | Every point checked (C64) | `test_location_integrity.py` | 31 files |
| 23 | Input | Emoji / RTL / 10 kB strings through the `$regex` filter | LIKE escaping; no unfiltered OR | `test_query_filters.py` | 32 files |
| 24 | Corporate | Two concurrent allowance approvals for one request | One grant | `test_allowance_race.py` | — |
| 25 | Safety | SOS insert fails (DB down) | Fallback path still notifies; loud error | `test_e2e_sos_flow.py` | B15 open |
| 26 | Mobile | App resumes after 30 min in background mid-ride; WS, push and REST disagree; slow `GET /rides/{id}` lands after `ride_started` (§2.1 M2) | Monotonic state; older event never overwrites newer | `rider-app/__tests__/rideStateReconcile.test.ts` | 10 files mention resume; version guard covers one event type |
| 26b | Mobile | Driver logs out on a shared device (§2.1 M1) | Persisted query cache cleared; no PII rehydrates | `shared/__tests__/queryClient.logout.test.ts` | none |
| 26c | Mobile | `stopLocationUpdatesAsync` throws on go-offline (§2.1 M3) | No pings in Period 0; notification text matches state | `driver-app/__tests__/backgroundLocation.offline.test.ts` | none |
| 27 | Ops | Loop leader lock unavailable for a money loop | Tick skipped (strict), counted | `test_leader_lock_strict.py` | none (no strict variant) |

---

## 7. 📈 Manager's Verdict

**Overall health: B-, holding — but the risk has moved.** On 2026-09-01 the
verdict was "the risk is not a bug, it is topology". Four days and 137 merges
later the topology fixes are *coded and dark* (worker tier, direct pool,
spatial providers, outbox) and the day-to-day fixes are landing at a high rate
(C54–C69 closed, admin god-pages decomposed, visual regression blocking,
loguru gate hardened, insurance-period alerting). What has not moved is the
release process, and the census shows the codebase accreting faster than it
is tidied (+78 swallowed exceptions, +36 dual imports, +8 prints, +296 lines
on the two largest files).

The deep dives also found five live-money and state defects that no earlier
pass caught — card riders are never charged the no-show fee while the driver
is still credited (§1.10 N2), a mid-trip stop edit silently drops the promo
discount and overcharges the rider (N3), a redelivered `payment_failed`
webhook can flip a paid ride back to unpaid (N1), a replayed dispute closes
its ledger entries twice (N4), and the batch-offer accept can race the expiry
reaper into offlining the winning driver mid-trip (§1.11 D1). Each is a
one-to-ten-line fix; together they are why Correctness drops half a grade.

The single highest-leverage change is not code: **make merge wait for green,
make deploy wait for merge, and get a second human approver.** Everything
else on this list is survivable for a quarter; shipping untested backend code
to production on every push is not, on a product taking real charges.

| Dimension | 09-01 | 09-05 | One line |
|---|---|---|---|
| Correctness | B+ | **B** | CAS guards are real and consistent; four money defects and one dispatch race on live paths; multi-row invariants still rest on reconcilers |
| Security | B+ | **B** | Pickup-OTP brute force, unverified admin JWT and empty `REDIS_URL` are all cheap and all still open |
| Error handling / telemetry | B | B | Envelope and scrubber excellent; swallow census growing; SLOs unwired |
| Performance | C+ | C+ | Now with evidence (910 ms); the fixes are built and dark |
| Architecture | C+ | **B-** | Worker, pool, geo framework and outbox exist in code; deploy them |
| Maintainability | C | C | Admin improved; backend god files grew; three root shims |
| Testing | B | B | Volume high; real-Postgres tiers wired; fixture cannot fail a race |
| Process | B+ | **C+** | Deploy on push, merge before CI, red `main` for 2.5 h, stale staging — the 09-01 grade credited the documents, not the enforcement |

### 30 / 60 / 90-day plan (each item ships alone, each has a verify step)

**Days 1–14 — stop the bleeding (no product risk)**
1. `deploy-fly.yml` → `workflow_run` on CI success; required checks + one
   approval in branch protection; fix the always-red Maestro workflow file
   and the two-dot risk labeler (§2 gap 8) so that red means red. *Verify:*
   a PR with a failing test cannot merge; a red `main` does not deploy; a
   docs-only PR is labelled `risk:low` and produces no failed run.
2. `release_command` for migrations; DSN as a Fly secret; staging to parity.
   *Verify:* `run_migrations.py --status` shows 0 pending on staging after a
   deploy.
3. `REDIS_URL` fail-fast validator; Redis socket timeouts; `/health` liveness
   split. *Verify:* boot with empty URL under `ENV=production` exits non-zero;
   breaker-open returns 200 degraded.
4. Pickup OTP validation + per-ride lockout; `jose.jwtVerify` in admin
   middleware. *Verify:* 6th wrong OTP → 423 and rider notified; forged admin
   token → `/login`.
5. WS-E admin `str(e)` redaction (E1–E8). *Verify:* guard test passes with an
   empty allowlist.
6. Lint blocking; coverage-regression blocking; `lint-staged --max-warnings 0`.
   *Verify:* budget number only goes down.
7. Money one-liners, each with its own Change Impact & Risk Log entry and a
   named regression test from the matrix (§6 rows 1b–1e): subtract the
   discount in `_reestimate_fare_for_stops` (N3); add the card branch to
   `mark_no_show` (N2); status-and-PI predicate on the `payment_failed`
   update (N1); unique index on `financial_events` (N4). *Verify:* the four
   new tests fail on `main` and pass on the branch; a staging no-show on a
   card rider produces a Stripe capture.
8. Dispatch race D1 (re-check `rides.driver_id` before penalising in
   `process_expired_offer`) and D5 (`.eq("is_online", True)` in
   `set_driver_available`). *Verify:* matrix rows 9 and 9c in the direct-pool
   tier.

**Days 15–45 — deploy what is built**
9. Fly `[processes]` worker group; strict lock on money loops; jitter on the 11
   loops; registry⇄lifespan test. *Verify:* `spinr_loop_*` gauges show each
   `deferred` loop on exactly one process.
10. Staging Scenario A/B with `dispatch_direct_pool_enabled` and provider
    `postgis`; then flip in production behind the flag. *Verify:*
    `spinr_dispatch_attempt_duration_ms{phase="claim"}` P95 halves.
11. Estimate path: shared httpx client, in-process polygon test, Redis cache for
    areas/vehicle types; OSRM for estimates. *Verify:* Scenario A estimate P95
    < 300 ms on the smallest tier.
12. Burn-rate alerts from `docs/slo.md` → PagerDuty; Loki via Alloy; `exec_ms`
    P95 alert. *Verify:* a synthetic estimate slowdown pages within 10 min.
13. Ride `version` on every WS event and REST body (D2) and the matching drop
    in the rider fetchers (M2); `shouldDehydrateQuery` allow-list plus cache
    clear on logout (M1); offline gate in the background-location task (M3);
    LogRocket options or removal (T6). *Verify:* matrix rows 26–26c; a shared
    device shows no prior driver's data after logout.

**Days 46–90 — structure and proof**
14. `pyproject.toml`, remove 1,020 dual-import blocks and the three root shims in
    one mechanical PR; `import-linter` contracts. *Verify:* grep counts in
    `docs/audit/2026-09-01-path-to-a-grade.md` §5 read 0.
15. OpenAPI → `shared/build-types` with a drift gate; mobile SDK diet.
    *Verify:* `any` < 300; two telemetry SDKs per app.
16. Typed flag layer with percentage/company targeting and flip audit; migrate
    the `*_enabled` keys. *Verify:* a money-path flag can be enabled for one
    corporate account only.
17. Edge-case matrix rows 1–16 as named tests; real-Postgres tier for
    settlement and `wallet_apply_delta`; `hypothesis` on the state machine;
    `pytest-xdist`; coverage gate 70. *Verify:* CI < 15 min at 70 %.
18. Split the six 2k+ backend files by sub-resource; move legacy import code
    under `scripts/legacy/`. *Verify:* no backend file > 1,200 lines.

### What was not verified
Live Supabase pooler/PostgREST pool sizes, the production value of
`settings.dispatch_geo_provider`, Railway's actual environment, production P95
for any SLA row (none exists in the repo), Grafana alert rules, the Vercel
deployment of the admin, the mobile apps at runtime, LogRocket's default
capture behaviour when initialised without options, whether a live Stripe
redelivery reproduces §1.10 N1 (reasoned from code, not exercised), and
`_ride_income`'s legacy fallback branch.
