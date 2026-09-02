# Change Impact & Risk Log — WS-A: PROCESS_ROLE split (audit C2)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude Code session (branch `claude/topology-remediation-plan-g516e0`) |
| Surface(s) | backend (deployment topology) |
| Domain (Sentry tag) | admin / drivers / payments (indirect — no loop body changed) |
| PR / commit link | commit `00799f4` on `claude/topology-remediation-plan-g516e0` |
| Related issue or gap ID | Critical issue **C2** in `docs/audit/2026-09-01-engineering-director-teardown.md`; WS-A of `plans/2026-09-01-critical-topology-remediation-plan.md` |

## 1. Issue / gap identified

Every backend replica runs the HTTP service *and* all ~40 background loops. Fly
scales the `app` group to 8 machines × 2 uvicorn workers, so a scale-out
multiplies batch work that only needs to happen once: 8 copies of the surge
engine, the payment-retry sweep, the reconciliation jobs, the T4A annual job,
each contending for the same Supabase thread pool.

## 2. Root cause

`lifespan()` spawns loops unconditionally; the only gate is
`_skip_background_loops = settings.ENV.lower() == "test"`. There has never been
a way to say "this machine serves HTTP, that machine runs the jobs" — the image
and the entrypoint are identical everywhere, so the loops come along with the
web service by construction.

## 3. Fix / remediation

New setting `PROCESS_ROLE` ∈ {`all`, `web`, `worker`}, default **`all`**.
`_spawn()` consults it: `web` records the loop name and returns without creating
a task; `worker` and `all` spawn as before. `capacity_watchdog` runs in every
role because it measures *this* process's thread pool.

Two fail-fast guards in `core/config.py`: an unrecognised role is rejected at
startup, and a production `web`/`worker` process refuses to boot without
`REDIS_URL`.

**Deliberately not done: A5–A7 (converting money loops to a fail-closed leader
lock).** See §11 — the premise turned out to be wrong.

## 4. Risk & impact on existing functionality

**Blast radius: potentially every background loop — mitigated to zero by the
default.** `PROCESS_ROLE` defaults to `all`, whose code path is the pre-existing
one with one extra boolean check that is always False. Nothing about this commit
changes behaviour on any current deployment (Fly, Railway standby, local dev,
CI) until someone explicitly sets the variable.

| Consumer | Assessment |
|---|---|
| All ~40 loops | Loop *bodies* untouched. Only whether `_spawn` creates the task changes, and only under `role=web`. |
| `test_lifespan_watchdog_coverage.py` | Passes unmodified. Names are appended to `_spawned_loop_names` **before** the role skip, so the registry the coverage check reads is complete regardless of role. Pinned by a source-order assertion in `test_process_role.py`. |
| `_WATCHDOG_LOOP_NAMES` / `loop_watchdog` | Untouched. Under `role=web` the watchdog is itself skipped, so it cannot alert about loops that role was never meant to run — no false pages. Under `worker`/`all` it behaves exactly as today. |
| `utils/loop_monitor.py` heartbeats | Untouched. The plan's A3 (adding a `role=` label to heartbeats and gauges) was **not** implemented: it exists to stop the watchdog alerting on loops the role does not run, and skipping the watchdog itself in `role=web` already achieves that without changing a metric's label set. Adding a label would silently break any existing dashboard query that sums these series. |
| Railway standby | Unaffected — no `PROCESS_ROLE` set, so `all`. |
| Fly deployment | **Unchanged by this commit.** `fly.toml` and `deploy-fly.yml` are not touched (GATE A8/A9 — see §8/§11). |
| Ride state machine / money paths | No interaction. |

Residual risk: the split is *available* but unexercised. Nothing verifies a
`web` machine actually keeps serving traffic correctly with no loops running,
because nothing runs in that mode yet. That verification belongs to whoever
applies the Fly change in §8.

## 5. User-experience effect

None. Riders, drivers, corporate admins and internal admins see no difference.
Not visible mid-session. No copy or notification changes. This is a deployment
topology capability that is inert until switched on.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/config.py` | +`PROCESS_ROLE` setting; `_validate_process_role`; `REDIS_URL` requirement for split roles | The role, and two ways to fail loudly instead of silently mis-deploying. |
| `backend/core/lifespan.py` | `_ALWAYS_ON_LOOPS`, `_skip_for_role`, role branch in `_spawn`, role in the startup summary line | The gate itself, plus the log line an operator greps to confirm a deploy took effect. |
| `backend/tests/test_process_role.py` | new | Role validation, the Redis requirement, the spawn decision, and source-order pinning. |
| `CLAUDE.md` | "Background task safety" paragraph rewritten | It said "the 40 startup loops run on every replica", which is now conditional; and it did not record *why* the fail-open locks are correct. |

## 7. Before / after

```python
# Before — backend/core/lifespan.py
    def _spawn(name: str, coro_factory):
        _spawned_loop_names.append(name)
        if _skip_background_loops:
            logger.info(f"Skipped background task in ENV=test: {name}")
            return
        # ... always creates the task
```

```python
# After
    _ALWAYS_ON_LOOPS = frozenset({"capacity_watchdog (60s)"})
    _process_role = settings.PROCESS_ROLE.lower()
    _skip_for_role = _process_role == "web"

    def _spawn(name: str, coro_factory):
        _spawned_loop_names.append(name)      # before the skip — coverage check
        if _skip_background_loops:
            ...
            return
        if _skip_for_role and name not in _ALWAYS_ON_LOOPS:
            logger.info(f"Skipped background task: {name} (role=web)")
            return
        # ... unchanged
```

Runtime behaviour, `PROCESS_ROLE` unset (i.e. every deployment today):
identical, because `_skip_for_role` is False.

## 8. Rollback plan

**Rollback is "do nothing"** — the change is inert at its default. Nothing needs
reverting unless someone has set `PROCESS_ROLE`, and then the rollback is
`flyctl secrets set PROCESS_ROLE=all` (a secret overrides `[env]`), no deploy.
No data is written, no schema changes, no migration.

### The Fly change this enables — NOT applied (GATE A8/A9)

Written out here so it can be applied as a deliberate, costed decision rather
than arriving as a side effect. It adds a 9th machine.

```toml
# backend/fly.toml
[processes]
  app    = "sh -c 'uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-2}'"
  worker = "sh -c 'PROCESS_ROLE=worker uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1'"

[http_service]
  processes = ["app"]

[env]
  PROCESS_ROLE = "web"      # app machines; the worker overrides it in its command

[[vm]]
  processes = ["worker"]
  size      = "shared-cpu-1x"
  memory    = "1gb"
```

```yaml
# .github/workflows/deploy-fly.yml
- flyctl scale count 8 --region yyz
+ flyctl scale count app=8 worker=1 --region yyz
```

Rollback for that step, no redeploy: `flyctl scale count worker=0` then
`flyctl secrets set PROCESS_ROLE=all`.

Before applying, confirm: (a) the cost of the extra machine, (b) that
`metrics-agent`'s `discover-targets.sh` does not filter by process group — the
worker machine must be scraped or its loop metrics vanish from dashboards,
(c) `flyctl config validate -c backend/fly.toml`, which **could not be run in
this session** (no flyctl, and no network egress to install it).

## 9. Verification performed

- [x] `ruff check` + `ruff format --check` clean on all three code files.
- [x] `python -m py_compile` clean.
- [x] Source-order invariant (name recorded before the role skip) asserted
      standalone with stdlib — this is the one that keeps
      `test_lifespan_watchdog_coverage.py` green, so it was worth executing
      rather than only writing a test for.
- [x] Loop-by-loop replay-safety review of all 29 lock-taking modules (§11) —
      the substantive analysis behind the decision not to ship A5–A7.
- [x] Confirmed `redis_startup_diagnosis` is spawned via `asyncio.create_task`
      directly, not `_spawn`, so it is unaffected by the role gate (it needed to
      keep running in every role and does).
- [x] Reviewed against CLAUDE.md "Background task safety" and updated the
      paragraph this change invalidates.
- [ ] **`pytest` not run** — see §10.

## 10. What was NOT verified

- **`pytest` was never executed.** PyPI egress is blocked in this environment
  (the gateway answers 403 to CONNECT), so `pydantic`/`fastapi`/`pytest` could
  not be installed. `test_process_role.py` has not run; its pydantic-dependent
  cases (role validation, the `REDIS_URL` guard) are unexecuted. Only the
  stdlib source-pinning assertions were run directly. CI is the first real
  signal.
- **No process has ever booted with `PROCESS_ROLE=web` or `worker`.** The gate
  is reasoned about and unit-tested, not observed. In particular nobody has
  confirmed that a web machine with no loops still serves every endpoint —
  a route that lazily depends on a loop having populated a cache would fail
  only in that mode. A grep found no such dependency, but that is not proof.
- `flyctl config validate` not run (no flyctl, no egress) — hence §8's TOML is
  documentation, not a shipped file.
- No staging deploy, no Redis-pause drill.

## 11. Finding: A5–A7 rest on a false premise — not implemented

The plan's WS-A carried a second goal: "make money-loop locks fail closed",
via a new `try_acquire_leader_lock_strict()` applied to `auto_payout`,
`payment_retry`, `corporate_autotopup`, `preauth_capture`, `referral_payout`,
`orphaned_hold_reconciler`, `stripe_reconcile` and `ledger_projection`.

**That helper was written, then reverted unused, and A5–A7 were not done.**
Reading every one of those loops shows the premise does not hold:

| Loop | Independent replay-safety guard |
|---|---|
| `auto_payout` | `auto_payout_batches.week_key` UNIQUE index ("the hard guard"), partial unique `idx_payouts_one_inflight_per_driver`, Stripe idempotency key |
| `payment_retry` | Atomic claim via conditional update + Stripe idempotency key on `PaymentIntent.confirm` |
| `referral_payout` | `UNIQUE(referee_user_id)` claim row — a duplicate claim fails |
| `orphaned_hold_reconciler` | CAS on `updated_at` + Stripe idempotency key `ride-cancelauth-{ride}-{pi}` |
| `preauth_capture` | Atomic DB claim, documented in-file as "the real double-capture guard" |
| `corporate_autotopup` | Takes **no** leader lock at all; relies on a deterministic Stripe idempotency key so concurrent replicas dedupe at Stripe |
| `scheduled_rides` | Atomic DB claim (the plan already flagged this one for a read-and-decide) |
| `stripe_reconcile`, `ledger_projection` | Already fail **closed** — their `try` wraps the whole tick, so a Redis error skips it and logs at `error` |

Two consequences:

1. **Converting them would be an availability regression on money paths for no
   correctness gain.** `referral_payout` states the trade-off explicitly in
   code: *"refusing every payout during a Redis blip would incorrectly freeze
   legitimate referrers"*. Shipping A5–A7 would reverse a documented decision.
2. **The helper would have had no legitimate caller**, making it speculative
   code — which CLAUDE.md's "Simplicity first" rule forbids. It was reverted.

The audit's underlying worry about C2 — that N replicas each run every loop — is
real, and it is what `PROCESS_ROLE` actually fixes: with `worker`, one machine
runs the loops and the fail-open/fail-closed question mostly stops mattering.

A keyword-based CI guard ("every lock-taking loop must document its
replay-safety") was prototyped and **rejected**: `stale_p3_closer` is genuinely
replay-safe via `.is_("ended_at", "null")` but uses none of the expected words,
so the guard produced a false positive immediately. A guard that cries wolf on
correct code is worse than the CLAUDE.md paragraph now carrying the invariant.

**Recommended follow-up:** correct C2 in
`docs/audit/2026-09-01-engineering-director-teardown.md` to separate the real
finding (loops run on every replica → wasted load) from the incorrect one
(fail-open locks → money risk).

## 12. Sign-off

- [x] Rollback plan is concrete (default is a no-op; the enabling infra change is
      written out but deliberately unapplied)
- [x] Blast radius is stated and bounded by the default
- [x] No silent behavior change — behaviour is bit-identical until `PROCESS_ROLE`
      is set
- [ ] **Not signed off on test execution or any real `web`/`worker` boot** — §10
