# Change Impact & Risk Log — DB deadline-rejection alert storm

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code (session: spinr-db-calls-rejected) |
| Surface(s) | backend, rider-app, driver-app (via `shared/`) |
| Domain (Sentry tag) | rides (request-path time budget; affects every mobile request) |
| PR / commit link | branch `claude/spinr-db-calls-rejected-mzusr7` — commits `7e049a9`, `a2c269c`, `17ee430`, `e600cbe`, `8d80047` |
| Related issue or gap ID | Operator report: repeated "*Spinr DB calls rejected* — 1 rejected since last tick (breaker state: `closed`, queue depth 0)" emails |

## 1. Issue / gap identified

Operators were receiving a steady stream of `capacity_watchdog` emails reporting
one rejected DB call per tick, with the circuit breaker **closed** and the thread-pool
queue at **0** — i.e. the alert fired continuously while the database was healthy.
Two distinct defects sat behind it: the alert was mis-scoped and over-sensitive, and the
condition it was reporting was a real (previously invisible) user-facing 503 bug.

## 2. Root cause

**Why the counter moved.** `spinr_db_calls_rejected_total` has four increment sites in
`backend/repositories/_base.py`. One is `reason=circuit_open`; the other three are
`deadline_exhausted` / `deadline_timeout`. Since the breaker was closed, every alert was
a **client-deadline** rejection, not a capacity event.

**Why deadlines expired on a healthy system.** `shared/api/client.ts` sent only
`X-Deadline-Ms`, an *absolute* epoch stamped from the device's `Date.now()`.
`DeadlineMiddleware` derived each request's budget as `deadline_epoch - server_now`,
subtracting the *server's* wall clock from the *device's*. Device clock skew therefore
landed directly in the budget:

- A handset ~15 s behind sends a budget that is already negative on arrival. Every
  `run_sync()` in that request is rejected pre-flight and the user gets a **503 on
  everything** for as long as their clock is wrong.
- The budget is also stamped before DNS/TLS/upload, so slow cellular (riders and drivers
  are in moving cars) spends it before the server begins work.

**A third contributor, found during the blast-radius sweep.** `utils/deadline.py` stores
the budget in a ContextVar explicitly so it propagates into `asyncio.create_task`. Work
backgrounded via `utils/background.spawn()` therefore inherited a budget measured from
when the *rider* started waiting; once the response was sent the budget was spent and
the backgrounded DB write was rejected. Silent loss of audit rows
(`routes/wallet.py`, `routes/payments.py`) and dispatch retries
(`routes/rides/matching.py`).

**Why so many emails.** Three multipliers on a threshold of one:

1. Signal 2 alerted on *any* increase (one rejection in 60 s), while the module's other
   two signals require `SUSTAIN_TICKS = 3`.
2. `_last_alerted` is a module-level dict, so the 30-minute cooldown was **per Python
   process**. `fly.toml` sets `UVICORN_WORKERS = 2` against an 8-machine pool → up to
   **16 independent alerters**, worst case ~32 emails/hour. Both workers on a machine
   report the same `FLY_MACHINE_ID`, so the duplicates were not even attributable.
3. `_send_email` sends one message per `ALERT_EMAIL_TO` recipient.

**Why it was un-triageable.** The alert summed across `reason` labels and printed a bare
total, dropping the one field that separates "the DB is dying" from "a handset's clock is
wrong". Worse, the two `deadline_exhausted` sites incremented the counter and raised a 503
with **no log line at all** — only `deadline_timeout` logged — so there was no way to trace
a rejection to an endpoint or a cause.

## 3. Fix / remediation

Five scoped commits:

1. **`7e049a9`** — all three rejection sites log with a comparable structured `extra`
   (reason, stage, retry_policy, overdue/waited seconds). No behaviour change.
2. **`a2c269c`** — `spawn()` clears the deadline ContextVar around `create_task`, so
   backgrounded work is bounded by its own logic rather than by how long a rider waited.
3. **`17ee430`** — `X-Timeout-Ms` (a *relative* duration, skew-immune by construction) is
   now preferred; the legacy absolute header is clamped to `[DEADLINE_MIN_MS,
   DEADLINE_MAX_MS]` (1 s…60 s) and clamps are counted via
   `spinr_deadline_header_clamped_total{direction,source}`. The client dual-ships both.
4. **`e600cbe`** — watchdog signal 2 split into 2a (`circuit_open`, still immediate) and
   2b (deadline rejections, now rate + sustain gated); alert body carries the per-reason
   breakdown; fleet-scoped signals dedupe across replicas via a shared Redis claim.
5. **`8d80047`** — client-side header contract tests.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface.** The deadline path is on *every* rider-app and driver-app
request (all five verbs in `shared/api/client.ts`). Greps performed and what they found:

| Searched | Result |
|---|---|
| `set_request_deadline` / `remaining_seconds` consumers | Only `repositories/_base.py::run_sync` reads the budget. No other consumer. |
| `X-Deadline-Ms` senders | `shared/api/client.ts` only (5 verbs). No admin-dashboard sender. |
| `deadline_monotonic` readers | Set on `request.state`, read by nothing outside the middleware. |
| `spinr_db_calls_rejected_total` increments | 4 sites, all in `_base.py`. |
| `background.spawn()` callers | ~20 across `routes/users.py`, `routes/auth.py`, `routes/rides/booking.py`, `routes/rides/matching.py`, `routes/admin/*`, `routes/corporate_company_bookings.py`. All are fire-and-forget work that *should* outlive the request — none depends on inheriting the caller's deadline. |
| Raw `asyncio.create_task` in request paths | ~25 sites still bypass `spawn()` and keep inheriting the deadline. **Not fixed here** — see §11. |

**Regressions considered:**

- *Clamping up could mask a genuinely-expired deadline.* Accepted: the floor is 1 s, so
  fail-fast still engages for anything slower; the alternative (obeying a skewed clock)
  is the bug being fixed.
- *`spawn()` detachment removes a bound on background work.* That bound was never
  intentional — it was ContextVar leakage — and it was silently dropping writes.
- *Fleet dedup could silence a real incident.* Mitigated by failing **open** on any Redis
  error, by releasing the claim when delivery reaches no channel, and by leaving the two
  genuinely per-process signals (`db_pool_saturation`, `db_circuit_open`) un-deduped.
- *Raising the alert threshold could hide a real regression.* Mitigated by logging the
  per-reason breakdown on **every** tick with rejections, regardless of whether it alerts.

**Background loops:** `capacity_watchdog` only. Still replay-safe — it writes no
application state; the Redis claim is a TTL'd advisory key, not a leader lock.

**Money / ride state / insurance periods:** untouched. No migration.

## 5. User-experience effect

- **Rider / driver with a skewed device clock:** currently gets a 503 on essentially every
  request. After this change their requests work. This is the user-visible win.
- **Rider / driver on slow cellular:** fewer spurious 503s near the edge of the budget.
- **Everyone else:** no visible change — a correct clock produces an unclamped budget
  identical to today's.
- **Mid-session visibility:** yes, and in the safe direction — a rider mid-ride on a
  skewed handset goes from failing to working. No copy or notification changes.
- **Operators:** alert volume drops from up to ~32/hour to at most 2/hour fleet-wide, and
  each message now names the reason and points away from a scaling reflex.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/_base.py` | Structured logging at all 3 deadline-rejection sites | Two of them were entirely silent, making the alert un-traceable |
| `backend/utils/background.py` | `spawn()` clears the deadline ContextVar around `create_task` | Backgrounded work must not inherit the client's budget |
| `backend/core/middleware.py` | `resolve_deadline_budget_ms()` pure helper; prefer `X-Timeout-Ms`; clamp + count; CORS allow-list | Removes clock skew from the budget calculation |
| `backend/utils/capacity_watchdog.py` | Signal 2 split 2a/2b; rate+sustain gate; per-reason body; Redis fleet dedup | Fixes the alert's sensitivity, its content, and its fan-out |
| `shared/api/client.ts` | Dual-ship `X-Timeout-Ms` alongside `X-Deadline-Ms` | Skew-immune spelling without a deploy-ordering dependency |
| `backend/tests/test_deadline_middleware.py` | New — 12 tests | Pins the skew and clamp cases |
| `backend/tests/test_background_deadline_detach.py` | New — 5 tests | Pins detachment and that `spawn()`'s contract is unchanged |
| `backend/tests/test_capacity_watchdog.py` | +6 dedup tests, signal-2 tests rewritten | Pins the storm's exact shape as a non-alerting condition |
| `shared/api/__tests__/client.deadlineHeader.test.ts` | New — 5 tests | Pins the client header contract |

## 7. Before / after

```py
# Before — backend/core/middleware.py: device clock minus server clock
deadline_epoch_ms = int(deadline_header)
now_epoch_ms = int(_t.time() * 1000)
remaining_ms = deadline_epoch_ms - now_epoch_ms      # skew lands here
monotonic_deadline = _t.monotonic() + (remaining_ms / 1000.0)
# handset 15s behind -> remaining_ms = -15000 -> every DB call 503s
```

```py
# After — relative header preferred, absolute one clamped
budget_ms, source, clamped = resolve_deadline_budget_ms(
    request.headers.get("x-timeout-ms"),      # relative: skew-immune
    request.headers.get("x-deadline-ms"),     # legacy: clamped to [1s, 60s]
    int(_t.time() * 1000),
)
if clamped is not None:
    _metrics.inc("spinr_deadline_header_clamped_total",
                 {"direction": clamped, "source": source or "unknown"})
```

```py
# Before — backend/utils/capacity_watchdog.py: any increase, any reason, per process
elif rejected_delta:
    await _post_alert("db_calls_rejected",
        f"*{int(rejected_delta)}* rejected since last tick "
        f"(breaker state: `{breaker_state}`, queue depth {queue_depth}).", webhook_url)
```

```py
# After — rate + sustain gated, per-reason breakdown, fleet-deduped
if DB_REJECTED_PER_MIN_THRESHOLD > 0:
    rejected_tripped = _sustained("db_calls_rejected",
        per_min is not None and per_min > DB_REJECTED_PER_MIN_THRESHOLD)
...
# and inside _post_alert:
if not await _try_claim_fleet_cooldown(signal):
    _last_alerted[signal] = now
    return
```

## 8. Rollback plan

Every behavioural change is reverted by an **env var + machine restart, no deploy**:

| Symptom | Revert | Effect |
|---|---|---|
| Deadline alerts now too quiet | `CAPACITY_DB_REJECTED_PER_MIN_THRESHOLD=0` | Restores pre-change any-increase immediate alerting |
| Fleet dedup suspected of hiding an incident | `CAPACITY_FLEET_DEDUP=off` | Restores per-replica alerting |
| Clamp floor/ceiling wrong for a real client | `DEADLINE_MIN_MS` / `DEADLINE_MAX_MS` | Widen or narrow without a deploy |

Code-level rollback is a plain `git revert` of any of the five commits — they are
independent and touch no persisted data, so no data-level remediation is needed. No
migration, no Stripe/wallet/ride-state effect.

The client change is additive (an extra header); a backend that ignores `X-Timeout-Ms`
falls through to the legacy path, so client and backend can ship in either order.

## 9. Verification performed

- [x] **Automated tests (unit):** `pytest tests/test_capacity_watchdog.py
      tests/test_deadline_middleware.py tests/test_background_deadline_detach.py
      tests/test_db_executor.py tests/test_db.py tests/test_db_circuit_breaker_probe.py
      tests/test_db_error_branching.py tests/test_db_supabase_helpers.py
      tests/test_csrf_middleware.py tests/test_middleware_user_id.py
      tests/test_middleware_production_config_guard.py
      tests/test_forced_upgrade_middleware.py` → **223 passed**.
- [x] **`spawn()` consumer regression sweep:** `test_admin_support_routes.py`,
      `test_dispatch_cascade.py`, `test_dispatch_db_errors.py`, `test_coverage_rides.py`,
      `test_create_ride_post_insert_branches.py`, `test_c2_driver_cancel_atomic.py`,
      `test_core_lifespan_coverage.py`, `test_admin_tax_id_import.py` → **333 passed**
      (combined with the new suites).
- [x] **A first implementation was caught by that sweep and rewritten.** Detaching by
      wrapping the coroutine broke 4 tests and errored 11 more (doubles that patch
      `asyncio.create_task` were handed a wrapper and never awaited the real work,
      surfacing only as a `RuntimeWarning`). Baseline was established by stashing to a
      clean tree (182 passed) to prove the failures were mine, then the approach was
      changed to clearing the ContextVar around `create_task`.
- [x] **Client tests:** `npx jest --roots ../shared --testPathPatterns deadlineHeader`
      from `rider-app/` → **5 passed**.
- [x] **Lint/format:** `ruff check` → all checks passed; `ruff format --check` → clean
      (one file reformatted, then re-tested).
- [x] **Typecheck:** `npx tsc --noEmit -p shared/tsconfig.json --ignoreDeprecations 6.0`
      → **no errors in `client.ts`**.
- [x] **Blast-radius greps:** listed in §4.
- [x] **Conventions reviewed:** dual-import pattern preserved in every touched backend
      module; no float money arithmetic introduced; no PII added to logs (the new log
      lines carry reason/stage/policy/seconds only — no coordinates, phone, name, email,
      or address); observability naming follows `spinr_<domain>_<metric>_<unit>`.
- [ ] **Feature-flagged:** not flag-gated. Justification: the two alerting changes carry
      env kill-switches (§8), and the deadline change is a bug fix whose "off" state is
      the broken behaviour. Flagging the header path would mean maintaining the
      skew-vulnerable branch in production.

## 10. What was NOT verified

State these plainly rather than letting the checklist above imply full coverage:

- **No production build was run for rider-app or driver-app.** `tsc --noEmit` on
  `shared/` and the jest suite are the only client-side signal. No `expo export`, no EAS
  build. The change is a two-line object literal in an existing helper, but per
  `CLAUDE.md` that is *not* equivalent to a production build and is stated as such.
- **Not tested against live Supabase or a real Fly deployment.** All backend tests use
  the `mock_supabase_client` fixture. The fleet-dedup path was exercised against a
  hand-written in-test fake Redis, **not** a real Redis and **not** a real multi-replica
  fleet. The 16-replica behaviour is simulated by re-entering `_post_alert` with
  different machine ids and cleared local state — it models the contention correctly but
  is not a distributed test.
- **The dominant `reason` in production is still unconfirmed.** The clock-skew mechanism
  is proven from the code path, but which of `deadline_exhausted` vs `deadline_timeout`
  actually dominates has not been read off a live `/metrics` scrape from this session.
  Run this before closing the loop:
  ```bash
  flyctl ssh console -a spinr-backend-yyz -C \
    "curl -s -H 'Authorization: Bearer $METRICS_AUTH_TOKEN' localhost:8000/metrics | grep spinr_db_calls_rejected"
  ```
  A `deadline_timeout`-dominant split would mean slow queries are a larger contributor
  than skew, and the threshold in §8 should be re-tuned accordingly.
- **No visual-regression coverage exists for any client surface** (rider-app and
  driver-app have none at all; admin-dashboard's Playwright job still has no committed
  baselines per `ACTION_ITEMS.md` B38). This change is not visual, but the standing gap
  is restated rather than silently relied upon.
- **`shared/api/__tests__` is not wired into CI.** These tests only run via an explicit
  `--roots ../shared` override, so they will not gate a future regression until that is
  fixed (§11).
- **~25 raw `asyncio.create_task` call sites still inherit the request deadline** (§11).

## 11. Follow-ups filed

1. Migrate the remaining raw `asyncio.create_task` call sites in request handlers to
   `utils.background.spawn()` so they also detach from the request deadline (and gain the
   strong-reference guarantee they currently lack).
2. Wire `shared/**/__tests__` into a CI job — they are currently unreachable by any
   workflow.
3. Once no deployed app build sends `X-Deadline-Ms`, drop the legacy branch and the
   clamp's `source="deadline"` path.

## 12. Sign-off

- [x] Rollback plan is concrete and testable — three env vars, no deploy.
- [x] Blast radius is stated, not assumed — grep table in §4, including the ~25 sites
      deliberately left unfixed.
- [x] No silent behavior change to an already-shipped flow — the UX field (§5) is filled
      in, and the one behavioural change users will notice is a skewed-clock device going
      from broken to working.
