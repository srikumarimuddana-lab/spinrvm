# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working Style

### Task decomposition (mandatory)
- Before starting any implementation, break it into subtasks of ≤ 3 files each.
- Use `TodoWrite` to track every subtask; mark done immediately after each commit.
- Never start the next subtask until the current one is committed.
- If a task touches > 5 files, use `/plan` to decompose it first.

### Context / token discipline
- Keep responses short; avoid re-reading files you already read this session.
- When context grows large (many tool calls in session), prefer targeted `grep`/`Read` with `offset`+`limit` over full file reads.
- If you hit a "prompt too long" error: stop, commit current work, summarize progress in one sentence, then continue in a fresh thought — do NOT retry the same giant prompt.

### Request timeouts
- For long-running bash commands (installs, full test suites), use `run_in_background: true`; never spin in a sleep loop waiting for output.
- Break large pip/npm installs into separate steps so a single timeout doesn't block everything.

### Batch size rule
- Limit each commit to one logical change. If a diff exceeds ~200 lines, split it.

### Change Impact & Risk Log (mandatory — product is in live app testing)

Spinr is currently going through live app testing with real users. Any commit or PR that fixes a bug, closes a gap, or changes existing behavior **must** include a Change Impact & Risk entry — do not just describe the fix, describe what it risks breaking. Use the template at `docs/templates/CHANGE_IMPACT_LOG.md` and either paste the filled-in table into the PR description or add it to `docs/change-log/` as `YYYY-MM-DD-<short-slug>.md` for anything touching a live-tested surface (rides, dispatch, payments, auth, corporate, safety).

Required fields per entry:
- **Issue/gap identified** — what's wrong today, one sentence.
- **Root cause** — why it happens, not just the symptom.
- **Fix/remediation** — what changed.
- **Risk & impact on existing functionality** — what else reads/writes the same table, state, or code path; what could regress. For a shared component/hook/utility, grep for every other consumer and name them — don't write "checked, looks fine" without listing who else is affected.
- **User experience effect** — rider/driver/corporate-admin/internal-admin facing change, if any, and whether it's visible mid-session to someone already using the app.
- **Files modified** — table: `file path | what changed | why`.
- **Before/after snippet** — for any behavior-changing diff (not pure additive code), a short before/after code block, not just the file link.
- **Rollback plan** — how to revert without a second deploy if it goes wrong (feature flag off, config revert, migration rollback SQL) — a `git revert` is not a rollback plan for anything already applied to live data (Stripe charges, wallet deltas, ride state).
- **Verification performed** — tests run, manual repro steps, staging check, and **whether a real production build was run** (`npm run build` / equivalent) for any `admin-dashboard`/`rider-app`/`driver-app` change — a passing dev server or `tsc --noEmit` alone is not equivalent, say explicitly which you ran.
- **What was NOT verified** — every fix has a real boundary of what was checked (e.g. "not tested against live Supabase, only mocked API responses" or "no visual regression tooling exists in this repo, so a visually-invisible change like `aria-label` was reasoned about, not screenshotted"). State it — don't let silence imply full coverage.

### Pre-merge release gates (mandatory while live app testing is active)

Do not rely on "commit, observe, roll back if broken" for anything touching a live-tested surface. Gate before merge, not after:

1. **Blast-radius check first** — before writing the fix, grep for every other caller/reader of the function, table, or state field being changed (for frontend, every other importer of a shared component/hook/utility). State the blast radius in the Change Impact Log, even if it's "isolated, no other callers." A stubbed-out component in a test gives zero real coverage of your change — say so if you find it, don't count it as "tested."
2. **Additive over destructive** — prefer a new column/field/flag over mutating an existing one when behavior might be observed mid-session (e.g. a rider mid-ride, a driver online). Never repurpose a column's meaning without a migration + dual-read window.
3. **Feature-flag anything user-visible and non-trivial**, and prefer additive/flagged rollout for anything touching a shared component used by 3+ pages — new/changed UX, new notification copy, new validation rules that could reject previously-valid input. Ship dark, verify in staging/canary, then flip on. This project's existing `app_settings`-in-DB pattern (see Critical Conventions) already supports flag-without-redeploy — use it; ask the user if unsure whether an equivalent mechanism exists for a given frontend surface.
4. **State-machine and money changes need a dry run** — any change touching ride state transitions, wallet/allowance deltas, or Stripe flows must be exercised against `mock_supabase_client` fixtures AND described with a concrete before/after scenario in the Change Impact Log, not just "tests pass."
5. **No silent behavior change to a live-tested flow** — if a fix changes what an already-shipped screen does (not just fixes a crash), that's a UX change and needs the "User experience effect" field filled in, even for an internal admin screen.
6. **If there's no automated visual/snapshot regression tooling for the surface you're touching, say so explicitly** rather than silently relying on "no visible diff" reasoning. Flag it as a standing gap (see `ACTION_ITEMS.md`) rather than re-discovering it every session.
7. **Rollback plan is required before merge, not written after something breaks** — if you can't state one, that's a signal that the change should be additive/flagged instead of a direct edit.
8. **A CI check that's red for a reason unrelated to your diff is a signal the gate itself has decayed, not "not my problem."** File a `[CR]` (see `.github/ISSUE_TEMPLATE/ci_change_request.yml`) for a documented accepted-risk finding rather than leaving a permanently-red gate unexplained. Don't force a fix that breaks something else just to turn a check green — verify a newer/patched dependency version actually works (run the affected build/lint/tests) before pinning it, since a version bump that "should" fix a finding can break the tool it's supposed to fix.
9. **Escalate, don't silently ship, when in doubt** — if blast radius is unclear, you can't verify all consumers of something shared (no time, no test coverage, unclear ownership), or the change touches rides/payments/auth/corporate/safety and you're not confident of the full impact, use `AskUserQuestion` before merging rather than shipping and watching for fallout.

### PR review handling (Codex auto-review)

> **Status as of 2026-08-01: no automated PR review is running on this repo, from either vendor.**
> - **Codex has been silent since 30 July.** The app is installed and has reviewed 183 PRs historically, but its last comment was on #2877 (created 2026-07-30). Roughly 200 PRs since (#2878 onward) have had none. Cause not yet diagnosed — see `ACTION_ITEMS.md` C9.
> - **The Claude agent audit (`claude-review.yml`) is off by design** — `ANTHROPIC_API_KEY` deliberately unset on cost grounds, see C7.
>
> The guidance below stays in force and applies unchanged if Codex resumes — but **do not wait for a Codex review that may never arrive**, and do not treat its absence as "no findings." Until one of the two is restored, a PR touching money, auth, migrations, dispatch, or safety should get a **manual** pass with `spinr-security-auditor` / `spinr-money-auditor` / `spinr-migration-reviewer` via the Agent tool before merge.

- When subscribed to a PR (or asked to look at one), **do not chase CI checks** — skip `yarn audit` / `npm audit` / lint / deploy status unless the user explicitly asks. Pre-existing dependency-audit failures on surfaces a PR doesn't touch are not this PR's job.
- **When a Codex review is present**, check its comments (`chatgpt-codex-connector`) and act on them without being reminded:
  1. For each unresolved Codex comment, verify the claim against the actual code — confirm it's true, partially true, or wrong.
  2. If true (or partially), fix it. If it's wrong or not applicable, leave it and say why in the reply.
  3. Reply to each thread (via `mcp__github__add_reply_to_pull_request_comment`) noting the fix commit SHA or the reason it needs no action.
  4. Commit + push fixes to the PR's feature branch; the PR updates automatically.
- Treat a verified Codex finding the same as any task: write/extend a regression test for it, run the affected tests, and keep the commit scoped to one logical change.
- Only escalate via `AskUserQuestion` when a fix is architecturally significant or genuinely ambiguous; otherwise just do it.

## Context Imports

Sprint-scoped and domain-deep context is loaded on demand, not baked into this file. Reference these when the task enters the relevant area:

- `@ACTION_ITEMS.md` — prioritized production-readiness backlog: pick open `[ ]` items from here; full context in `docs/PRODUCTION_READINESS.md`
- `@.claude/context/sprint-current.md` — active sprint goal, in-flight tickets, blockers
- `@.claude/context/domain-dispatch.md` — dispatch algorithm, driver matching, offer timeout
- `@.claude/context/domain-payments.md` — fare calc, surge, Stripe flows, corporate billing
- `@.claude/context/domain-corporate.md` — corporate account/membership/policy lifecycle, cascade-effect checklist, flag conventions
- `@.claude/context/domain-safety.md` — SOS, insurance periods, emergency flows
- `@.claude/context/regulatory-sk.md` — Saskatchewan Transportation Act obligations
- `@.claude/context/brand-spinr.md` — brand colors, typography, and logo assets; load for any customer-facing marketing/creative work

## Project Overview

Spinr is a Canadian ride-sharing platform (Saskatchewan-first, 0% driver commission), split across `backend/`, `rider-app/`, `driver-app/`, `admin-dashboard/`, and `shared/` — see each surface's manifest for its tech stack.

## Commands

### Backend (Python/FastAPI)

```bash
cd backend
pip install -r requirements.txt
python3 -m backend.server          # run from repo root
pytest                             # full suite with coverage
pytest -m unit                     # unit tests only
pytest -m "not slow"               # skip slow tests
ruff check .                       # lint
ruff format .                      # format
```

### Database Migrations

```bash
cd backend
python scripts/migrate.py            # ordered SQL runner over backend/migrations/; no --env flag —
                                      # environment is selected by whichever SUPABASE_URL /
                                      # SUPABASE_SERVICE_ROLE_KEY (or PG_CONNECTION_STRING /
                                      # DATABASE_URL) are set when it runs. Add --dry-run to preview.
                                      # The direct db.<ref>.supabase.co host is IPv6-only; on
                                      # IPv4-only networks set PG_CONNECTION_STRING to the Session
                                      # pooler connection string instead (takes precedence).
```

## Architecture

### System Topology

```
Rider App ──┐
Driver App ─┤── REST + WebSocket ──► FastAPI (Fly.io primary / Railway standby)
Admin ───────┘                            │
                             Supabase(Postgres+RLS)  Redis  Stripe
                             Firebase  Twilio  FCM
```

Backend is a single horizontally-scalable process. All durable state lives in Supabase; ephemeral cache/pub-sub lives in Redis. WebSocket fan-out across replicas uses the `spinr:ws:dispatch` Redis pub/sub channel.

### Key Backend Files

- `backend/server.py` — app factory; mounts ~25 routers
- `backend/core/config.py` — pydantic-settings `Settings`; fails fast in production on weak secrets
- `backend/core/lifespan.py` — startup/shutdown: DB health check + spawns 17 background asyncio loops (subscription expiry, surge engine, scheduled dispatch, payment retry, document expiry, corporate auto-topup, low-balance nudge, allowance reset, safety check-in, retention purge, reconciliation, Stripe reconcile, T4A annual job, driver earnings statements, stuck-ride sweeper, push retry, loop watchdog)
- `backend/core/middleware.py` — CORS, security headers, rate limiting (SlowAPI + Redis)
- `backend/db_supabase.py` — ~66 helper functions wrapping `supabase-py` via `run_sync()` (thread-pool with one retry on H2 GOAWAY)
- `backend/socket_manager.py` — `ConnectionManager` (in-process WS registry); delegates to Redis pub/sub when active
- `backend/routes/` — one file per domain (rides, drivers, auth, payments, wallet, corporate, fares, notifications, websocket, …)
- `backend/routes/admin/` — 15+ admin-only endpoints
- `backend/services/` — thin service layer for dispatch, fare, corporate wallet/membership/allowance
- `backend/utils/` — cross-cutting: `surge_engine.py`, `redis_client.py`, `ws_pubsub.py`, `rate_limiter.py`, `crypto.py`, `audit_logger.py`, `payment_retry.py`, `scheduled_rides.py`

## Critical Conventions

**Money arithmetic** — use Python `Decimal` only (never float). Helpers `_d()`, `_round()`, `_f()` must be used before any DB write or API response. A pre-commit hook blocks float arithmetic in fare code.

**Dual import pattern** — every backend module uses:
```python
try:
    from .routes.rides import ...
except ImportError:
    from routes.rides import ...
```
This is intentional (`python -m backend.server` vs top-level). Do not simplify away.

**Ride state machine** — always guard transitions with `_require_ride_in_state()`. `cancelled` is only valid before `in_progress`. State changes must emit a WebSocket event.

Valid states and transitions (source: `backend/routes/rides.py`):

```
                ┌─► cancelled (rider/driver/system, pre-trip only)
                │
scheduled ──► searching ──► driver_assigned ──► driver_accepted ──► driver_arrived ──► in_progress ──► completed
                │                │
                │                └─► searching (offer timeout, ~15s, releases driver)
                │
                └─► cancelled (auto, no drivers found after ~5min)
```

Invariants:
- `active_statuses = ["searching", "driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]` — a rider may have at most one active ride at a time
- Transitions from `in_progress` are `completed` only. Never `cancelled` after trip start.
- `scheduled` rides skip `searching` until their dispatch time, then enter `searching` via the scheduled-dispatch background loop
- Race on acceptance: Supabase update filters `{'status': 'searching'}` — 0 rows → ride taken → 409 + `ride_taken` WS event
- Every state change must emit a WebSocket event keyed to both the rider and the driver connection (if assigned)

When writing code that reads `ride.status`, treat any value not in the set above as a contract violation — surface loudly.

**Race condition guard for ride acceptance** — the Supabase update filters on `{'status': 'searching'}`. Zero rows returned → ride already taken → send `ride_taken` WS event, return 409.

**JWT trust model** — admin JWTs are fully trusted (role+email+modules in claims). Rider/driver role is always re-read from the `users` table on every request; never trust the JWT role claim for non-admin tokens.

**Driver online/available flags** — `is_online` is driver-toggled (a driver tapped "Go online"); `is_available` is system-computed (`is_online AND not on active ride AND not in offer-pending`). The invariant **`is_available ⇒ is_online`** must hold; the inverse does not. Dispatch reads `is_available`; admin filters read `is_online`. Never set `is_available = True` without `is_online = True`.

**Stripe idempotency** — call `claim_stripe_event(event_id)` in the `stripe_events` table before processing any webhook; silently skip if already claimed.

**OTP security** — OTPs are SHA-256 hashed at rest; 5 failures/hour triggers a 24-hour Redis lockout. Dev bypass `"1234"` only works when `ENV != production`.

**Redis transparency** — `utils/redis_client.py` falls back to an in-process dict when `REDIS_URL` is unset. Rate-limit and OTP lockout state are lost on restart in this mode.

**WebSocket auth** — first message must be `{"type": "auth", "token": "<jwt>"}`. Connection keys: `"driver_{user_id}"` / `"rider_{user_id}"`. 30-second ping heartbeat; 30 msg/s rate limit; 64 KB max message.

**Background task safety** — the 16 startup loops run on every replica concurrently. Dispatch uses an atomic DB claim; others use `reminder_sent` flags or idempotency keys. Any new loop must be replay-safe.

**Settings in DB** — Stripe keys, Twilio credentials, and Google Maps API keys live in the `app_settings` Supabase table (managed via admin dashboard), not in `.env`. This allows rotation without redeployment.

**Query filters — the layer owns escaping, callers pass raw input.** The filter dicts accepted by `db_supabase.get_rows`/`update_one`/`delete_many` are Mongo-shaped but compile to PostgREST, and `$regex` compiles to a SQL `ILIKE '%term%'` — **not** to a regex. Rules:
- Never `re.escape()` a search term before putting it in a `$regex`. It leaks regex escapes into the LIKE pattern (`re.escape("Nighil Kumar")` → `Nighil\ Kumar`, which matches a literal backslash and so matches nothing). `repositories/_base.py` handles LIKE-wildcard escaping (`_escape_like`) and PostgREST quoting (`_postgrest_or_value`) for both the `$or` and non-`$or` paths.
- A predicate the OR builder cannot express **raises**; it is never dropped. A dropped leaf widens the OR, and because `_apply_filters` is shared with update/delete, an `$or` whose leaves all vanished would have matched the whole table. Add the operator to `_build_or_clause_term` rather than working around it.
- A name/email lookup that spans two tables (e.g. drivers ← users) must resolve IDs in a first query and pass them as `{"col": {"$in": ids}}` — PostgREST cannot filter a parent by an embedded child. Guard the empty-ID case in the caller; an all-empty `$or` raises rather than issuing an unfiltered query.

**Corporate billing layer** — sits on top of the consumer ride product without modifying ride/driver logic. Payment source selection (rider wallet / card / company allowance / master wallet fallback) happens at fare settlement. All wallet deltas go through the `corporate_wallet_apply_delta` Postgres function for row-level locking and idempotency.

**Surge pricing rules** (source: `backend/utils/surge_engine.py`):

Auto-mode tiers (demand / supply ratio → multiplier):
| Ratio | Multiplier |
|---|---|
| < 0.5 | 1.0× (normal) |
| 0.5 – 0.8 | 1.25× |
| 0.8 – 1.2 | 1.5× |
| 1.2 – 2.0 | 1.75× |
| 2.0 – 3.0 | 2.0× |
| ≥ 3.0 | 2.5× (HARD CAP) |

- `SURGE_CAP = 2.5` is the ceiling for auto mode. Never suggest raising it without explicit business + legal review.
- Surge engine runs every 2 minutes; updates only service areas where `surge_source == 'auto'`
- Admin manual override accepts 1.0–10.0 but any value > 2.5 requires documented justification (regulatory + reputational risk)
- Surge must be visible to the rider *before* booking — never apply retroactively
- Never apply surge to scheduled rides booked outside the surge window
- Surge does not apply to corporate account-paid rides (policy; verify in fare service)

**Token lifetimes** — access tokens: 15 min (rider/driver), 1 hr (admin). Refresh tokens: 30 days, stored as SHA-256 hash, rotated on every use. Mobile clients auto-retry 401s via Axios interceptor after token refresh.

**Insurance periods (TNC commercial insurance)** — every moment a driver spends in the app maps to one of four periods. Misclassification is a regulatory and insurance liability. Derive period from ride state, not from the driver UI:

| Period | Driver state | Ride state | Insurance layer |
|---|---|---|---|
| 0 | App off / offline | — | Personal auto only |
| 1 | App on, available | No assigned ride | TNC contingent liability |
| 2 | En route to pickup | `driver_assigned` or `driver_accepted` or `driver_arrived` | TNC primary commercial |
| 3 | Passenger aboard | `in_progress` | TNC primary commercial (full coverage) |

Rules:
- Every period transition is logged to `driver_insurance_periods` with `{driver_id, period, started_at, ended_at, ride_id?}` for regulatory audit
- Never delete or mutate period rows — append only
- Period 2 starts on `driver_assigned` (not `driver_accepted`) because the driver is already obligated to the ride
- A driver cannot be in Period 3 without a `ride_id` linking to an `in_progress` ride
- Document expiry (license, insurance, vehicle registration) blocks Period 1+ — checked on every `go_online` call

**Do not silently swallow errors** — especially DB, auth, payment, and dispatch errors. These are crucial to the system and must surface loudly so the root cause can be fixed, not masked. Rules:
- Never replace a failing call with a generic fallback path that hides the symptom (e.g. don't fall through to "create new user" when `get_user_by_phone` raises — that produced duplicate accounts).
- Never `logger.warning(...)` and continue on a DB/auth/payment error. Use `logger.error(...)` with the full underlying exception (for `DatabaseError`, include `e.details["original"]` — `str(e)` alone gives only "Database operation failed").
- Return a clean `HTTPException` (usually 503 for DB, 502 for upstream) so the client retries, instead of handing back a half-valid response.
- Before silencing or softening any error during development, STOP and ask the user. "Soft-handling" is a trade-off they get to decide, not a default.

## Database & Migration Conventions

Migrations live in `backend/migrations/` and are applied in filename order by `backend/scripts/migrate.py`.

Naming: `NN_short_description.sql` where `NN` is a zero-padded sequence number — check the current highest with `ls backend/migrations | sort -V | tail -1` before picking the next one. Pick the next available number — never reuse or reorder existing numbers. If two PRs conflict on a number, the second one renames to the next free slot before merge. Note: the runner uses the full filename as the idempotency key, so already-applied migrations must never be renamed. Duplicate numeric prefixes exist from history and are handled by full-filename keying — do not introduce new duplicates; a CI prefix-uniqueness check blocks them.

Full conventions (append-only rule, RLS pattern, table naming, index rules): see `backend/migrations/CLAUDE.md`.

## Observability Conventions

Logging:
- Python: `logger = logging.getLogger(__name__)` per module. Use structured context via `extra={...}`.
- Log levels: `error` for actionable failures, `warning` for recoverable anomalies, `info` for state transitions, `debug` gated behind env flag.
- Never `print()` in production code. Never `logger.warning(...)` and continue on a DB/auth/payment error.

Sentry tags (attach to every captured event):
- `domain`: one of `dispatch`, `payments`, `auth`, `corporate`, `safety`, `drivers`, `rides`, `admin`, `ai`
- `surface`: one of `backend`, `rider-app`, `driver-app`, `admin`
- `ride_id`, `driver_id`, `rider_id` — only IDs, never PII
- `env`: `production` / `staging` / `development`

Metric naming — Prometheus/OpenMetrics snake_case `spinr_<domain>_<metric>_<unit>`
(counters end `_total`, latency histograms end `_duration_ms`). The metric names
themselves are defined at their emitting call sites (e.g.
`services/dispatch_service.py`, `services/payment_service.py`,
`utils/stripe_reconcile.py`); `utils/metrics.py` provides the underlying
counter/gauge registry and the exposition format `utils/metrics.render_prometheus`
emits. Dashboards/alerts must use these names (the older dotted
`spinr.<domain>.<metric>.<unit>` spelling is **not** what the code emits — do not
write alerts against it):
- `spinr_dispatch_offer_sent_total`
- `spinr_dispatch_offer_accepted_total`
- `spinr_dispatch_offer_to_accept_duration_ms`  (KPI: P95 < 2s dispatch latency)
- `spinr_dispatch_presence_filter_failed_total`  (Redis-presence degradation)
- `spinr_fare_calc_duration_ms`
- `spinr_payment_settlement_total{outcome=success|failed|retry}`
- `spinr_ws_fanout_duration_ms`

What to log vs metric vs Sentry:
- State transitions → info log + metric
- User-visible errors → Sentry (with domain tag) + error log
- Degraded-but-recovered → warning log + metric (never Sentry — noise)
- Security-relevant events (auth failures, RLS denials, admin actions) → audit table + info log

## Testing Conventions

Test files live in `backend/tests/` (Python) or `<app>/__tests__/` (RN/Next.js).

Python:
- Use `@pytest.mark.anyio` for async tests (loaded explicitly in `conftest.py`)
- Mock Supabase via the `mock_supabase_client` fixture in `conftest.py` — don't hit the real DB in unit tests
- Data factories live in `backend/tests/_factories.py` (kept out of `conftest.py` because pytest loads conftest by file path, which breaks `from tests.conftest import ...`)
- Patch target for DB is the `supabase` binding **in the module that defines the function under
  test**, not `backend.db_supabase.supabase`. `db_supabase.py` only re-exports, so
  `db_supabase.update_one` *is* `repositories._base.update_one` and reads `_base`'s globals —
  rebinding `backend.db_supabase.supabase` has no effect on it. Use
  `backend.repositories._base.supabase` for the generic CRUD helpers (`get_rows`, `update_one`,
  `insert_one`, …) and the matching `repositories.<domain>_repo.supabase` for domain functions.
  `conftest.py` patches all of them for this reason; see its comment on why both spellings exist.
- Use `pytest -m unit` for fast local loop; `pytest -m "not slow"` in pre-push; full suite in CI

Test tiers:
- **Unit**: single function, all deps mocked. Target: < 100 ms per test.
- **Integration**: real Supabase against a throwaway test schema. Target: < 2 s per test.
- **E2E (ride lifecycle)**: full searching → completed flow with mock payments. Keep in `test_e2e_*.py`.
- **Performance (perf_baseline.py)**: benchmark critical paths; compare against `perf_*_before.json` baselines to detect regressions.

Coverage minimums (per domain):
- `routes/payments.py`, `services/fare_service.py`, `utils/crypto.py`: ≥ 90%
- `routes/rides.py`, `services/dispatch_service.py`: ≥ 80%
- `routes/corporate_*.py`, `services/corporate_*.py`: **target ≥ 80%** (same tier as rides/dispatch — moves real money via `corporate_wallet_apply_delta`). As of 2026-07-28 the module averages ~52% aggregate (new code from the corporate lifecycle audit is 79–90%; pre-existing files like `corporate_accounts.py` at 39% and `corporate_signup.py`/`corporate_rider.py`/`corporate_company_kyb.py` at 32–33% are the gap). Not yet enforced by a `--cov-fail-under` gate on this module specifically — closing it is tracked as its own backlog item, not blocking new corporate PRs in the meantime.
- Admin routes, utilities: ≥ 70%

What must have a test:
- Every new state transition (add a case to `test_ride_state_machine.py`)
- Every fare calculation branch (tiers, surge, corporate, promo)
- Every auth/RLS policy (both allowed and denied paths)
- Every Stripe webhook type before hitting production

## Performance SLAs

Target P95 latencies per critical path. Code that risks breaching them should be flagged in review:

| Path | Target P95 | Failure impact |
|---|---|---|
| Dispatch offer → driver phone notification | < 2 s | Ride abandonment |
| Fare estimate (rider tap → price shown) | < 300 ms | Booking friction |
| Fare settlement (trip end → receipt) | < 1 s | Rider wait on arrival |
| WebSocket event fan-out (backend → client) | < 100 ms | Missed state updates |
| Driver location update (write) | < 150 ms | Stale ETA |
| Auth token refresh | < 200 ms | UX stutter |
| Stripe webhook processing | < 500 ms | Payment backlog |
| Migration apply (prod window) | < 30 s | Deploy stall |

Anti-patterns that reliably breach SLAs:
- N+1 Supabase reads in a loop (batch via `.in_()` instead)
- Awaiting Twilio/Stripe inline in a request handler (queue via `asyncio.create_task` or background worker)
- Reading full ride list on dashboards without pagination
- WebSocket broadcast to all connections instead of targeted fan-out

## Saskatchewan Regulatory

Spinr operates under Saskatchewan Government Insurance (SGI) and the province's ride-share regulations. The detailed checklist lives in `@.claude/context/regulatory-sk.md`; the non-negotiables appear below.

Driver eligibility (enforced at onboarding + every `go_online`):
- Valid Class 5 driver's license (standard) — Class 1-4 drivers need separate approval
- Minimum 3 years licensed driving experience
- Clean abstract: no major violations in past 3 years, no Criminal Code driving offences
- Vehicle < 10 years old; passes annual inspection
- Ride-share endorsement on insurance (SGI Auto Fund)
- Criminal Record Check + Vulnerable Sector Check on file, renewed annually

Trip log retention (regulatory, cannot be overridden by PIPEDA deletion requests):
- Trip record: 7 years (financial/tax)
- Driver/vehicle linkage at trip time: 7 years
- GPS trace at pickup and dropoff (not entire route): 3 years
- Insurance period transitions for commercial coverage audit: 7 years

Tax:
- Rider receipts must show GST (5%) and PST (6% where applicable) as separate line items
- Driver earnings summary must be T4A-compatible at year end (annual threshold applies)
- Fare floor subject to municipal minimum (if any); surge cap is provincial (ours is tighter at 2.5×)

Accessibility:
- Wheelchair-accessible vehicle (WAV) requests must be supported if a WAV driver is online in the service area
- Service animal accommodation is mandatory; drivers cannot refuse
- App must meet WCAG 2.1 AA for customer-facing surfaces

Driver classification:
- Drivers are independent contractors, not employees. Language in onboarding, training, and app copy must reflect this. Any control-of-work language (mandatory shifts, required uniforms, employee benefits) triggers re-classification risk and must be reviewed before shipping.

## Compliance (PIPEDA)

Spinr operates under Canada's Personal Information Protection and Electronic Documents Act. Every data flow must respect these rules:

Data minimization:
- Collect only what is needed to provide the ride. Tie every new field to a stated purpose.
- Driver's full address: needed for background check → stored encrypted in `drivers`. Not shared with riders.
- Rider's home/work addresses: stored only if saved as a favorite by the user. Never inferred.

What can NEVER appear in logs, Sentry events, or analytics payloads:
- Raw GPS coordinates (lat/lng) — log geohashed area at most
- Full phone numbers — use last-4 if you must (`phone_last4`)
- Full names — use user_id
- Email addresses — use user_id
- Payment card numbers — Stripe handles; never log even masked PANs
- Government IDs, SIN, driver license numbers
- Exact pickup/dropoff addresses — log city/area only

Data residency:
- Supabase project must be in a Canadian region (ca-central-1 or equivalent). Changing regions is a compliance event — never do without legal sign-off.
- All primary storage (Stripe customer data, Firebase) must be region-matched or justify exception.

User rights:
- **Access**: rider/driver can request full data export via Support → backend generates JSON dump
- **Correction**: profile fields are self-serve; non-trivial corrections go through Support
- **Deletion**: right-to-delete retains only what the Saskatchewan Transportation Act requires (see regulatory section below). All other PII is scrubbed within 30 days. Ride records become anonymized (user_id nulled, coordinates rounded to city).
- **Consent**: consent language version is stored on signup. Material changes require re-consent.

Breach protocol:
- Any suspected PII exposure (wrong user saw another user's data, leaked logs, RLS bypass) is a P0 incident
- Within 24h: scope assessment, log capture, preserve evidence
- Within 72h: Privacy Commissioner notification if the breach poses "real risk of significant harm"
- See `docs/runbooks/data-breach.md` for the full procedure

## Required Environment Variables

**Backend** (`backend/.env`):
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- `JWT_SECRET` — must be ≥32 chars; startup fails in production with weak value
- `FIREBASE_SERVICE_ACCOUNT_JSON` — stringified JSON
- `ADMIN_PASSWORD` — must not be `admin123` in production
- `REDIS_URL`, `RATE_LIMIT_REDIS_URL`, `WS_REDIS_URL` — optional in dev (in-memory fallback)

**Rider App** (`rider-app/.env`):
- `EXPO_PUBLIC_BACKEND_URL`
- `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`

## What Spinr Is NOT

Guardrails against accidentally turning Spinr into a generic Uber clone. Suggestions that violate these should be flagged, not implemented:

- **Not** a commission-taking marketplace. Driver keeps 100% of the fare. Monetization is SaaS corporate accounts + premium rider features + partner referrals — never per-trip cuts on consumer rides.
- **Not** a surge-first product. Hard cap of 2.5× auto surge. Never introduce "dynamic pricing" that behaves like unbounded surge. Never hide surge before booking confirmation.
- **Not** a driver-control platform. Drivers are contractors; we don't dictate shifts, mandate uniforms, or penalize offline time. Features that nudge toward control-of-work patterns require legal review.
- **Not** a data-harvesting product. Analytics exists to improve matching and safety — not to build advertising profiles. Never add third-party ad SDKs or behavioral retargeting.
- **Not** a hidden-fee operator. Every charge on the receipt maps to a disclosed line item: base fare, distance, time, booking fee, surge, tax, tip. Never add a "service fee" to mask the monetization model.
- **Not** a country-agnostic product. Canadian regulatory context (PIPEDA, Saskatchewan Transportation Act, SGI insurance) is baked into the design. Features that don't work in that context aren't features.
- **Not** a 911 replacement. SOS notifies emergency contacts and our safety team and *offers* one-tap 911; it never auto-dials and never claims to replace calling emergency services.

## KPI Targets

Production health is measured against these targets. Code that risks breaching them should be flagged in review. Pull current values via `/kpi`.

| Metric | Target | Below-target signal |
|---|---|---|
| Match rate (rides requested → driver accepted) | ≥ 85% | Dispatch radius too tight or driver supply gap |
| Rider cancellation rate | ≤ 8% | Long wait or wrong ETA |
| Driver cancellation rate | ≤ 3% | Offer quality or fare transparency issue |
| Driver utilization (on-trip time / online time) | ≥ 55% | Supply-demand mismatch; surge engine should activate |
| P95 dispatch latency (offer → accept) | < 2 s | Matching engine or WS latency |
| P95 fare calc latency | < 300 ms | Upstream (Google Maps) or logic bloat |
| Payment success rate | ≥ 99% | Stripe config, card decline, 3DS flow |
| Weekly active driver retention (week-over-week) | ≥ 80% | Earnings, UX, or support issue |
| Safety incident rate | < 1 / 10k rides | Investigate every incident individually |
| Support ticket response (P1) | < 2 h | Staffing or playbook gap |

## Deployment

- **Backend**: deployed to **both** Railway (Canada) and Fly.io (`yyz`, Toronto) from `main` in parallel *by design*. Fly.io is the intended primary; Railway is the warm standby. Routing is a Cloudflare CNAME on `api-spinr.spinr.ca` — fail-over/fail-back is a single DNS change (no load balancer). Shared Redis sits behind a `redis.spinr.ca` DNS alias so the Redis backend can be repointed on fail-back. See `docs/runbooks/railway-fly-failover.md` and `docs/adr/007-fly-primary-railway-standby.md`. **Current status: degraded** — Railway's `deploy-backend.yml` is blocked by a GitHub Environment protection rule (undated "temporary" pause), so Railway has been silently drifting from `main`; a Fly outage today would fail over to a stale build. Tracked in `ACTION_ITEMS.md` C5 — check there before assuming standby is live.
- **Frontend/Admin**: Vercel
- **Mobile builds**: Expo EAS — only triggered when commit message contains `[build]`

Agent Framework (`agents/`) — a separate Python SDK for multi-agent development automation, **not part of the production runtime**. Conventions: `agents/CLAUDE.md`.

## Claude-Adjacent Directories

These directories exist alongside `.claude/` but serve different tooling:

| Directory | Status | Purpose |
|-----------|--------|---------|
| `.kilo/` | Active | Kilo Code AI assistant config |
| `.emergent/` | Active | Emergent AI agent config |
| `.maestro/` | Active | Maestro orchestration config |
| `audit-framework/` | Active | Shared audit scripts for all AI assistants |
| `memory/` | Stale | Originally for agent memory; contains only `.gitkeep` — can be archived |
| `discovery/` | Stale | Early Expo sandbox; unreferenced by any surface — can be archived |
