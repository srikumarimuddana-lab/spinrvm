# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (claude-sonnet-5), session `session_01PPGd1wK6WRzbtxcGj3qNkX` |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (loop observability; touches loops tagged dispatch/safety/payments/corporate indirectly via their threshold values, not their business logic) |
| PR / commit link | branch `claude/fix-loop-watchdog-thresholds`, commits `8ed6d31`, `716b3da`, `7d0c913` (not yet pushed/opened as a PR — pushed by the orchestrator) |
| Related issue or gap ID | REL-001 (`docs/audit/clean-sheet/02-findings/reliability.md`), ROADMAP N17 |

## 1. Issue / gap identified

24 of the 44 background loops in `backend/core/background_loop_registry.py`'s
`LOOP_CATALOG` had no explicit entry in `backend/utils/loop_monitor.py`'s
`LOOP_THRESHOLDS`, so they silently fell back to the generic 2-hour
`_DEFAULT_THRESHOLD` — far too loose for 6 of them (safety/dispatch loops
that tick every 10-60s, where a hang could go undetected for up to 2h), and
simply untuned for the rest. Separately, `t4a_annual_job.py`'s loop polled
every 60s but never called `record_heartbeat` at all, so it sat permanently
in `get_loop_status`'s `"never_ticked"` state — the one state the function
explicitly does *not* flag as unhealthy — meaning the loop-watchdog had no
mechanism that could ever flag a crashed/hung T4A job, a CRA-regulatory
annual tax-slip issuance job.

## 2. Root cause

`LOOP_THRESHOLDS` was hand-populated per loop only as each was added or
specifically fixed (e.g. the prior 4-worker-wave-loop and 13-loop
watchdog-registration fixes referenced in the file's own history), with no
test tying every `LOOP_CATALOG` entry to a threshold entry. `t4a_annual_job`
was written with a fast 60s inner poll (correctly, to stay
restart-responsive) but the `record_heartbeat` call itself was simply never
added when the file was written.

**Re-verification note:** the source audit finding (REL-001, written
2026-09-23 per its own evidence citations) claimed ~30 mistuned loops
including several "Direction B" cases (`subscription_expiry`,
`document_expiry`) that this session found were **already fixed** in the
current code (both already carry explicit, correctly-sized threshold
entries — `12h`/`24h` respectively — as of commit `c48f031`, 2026-09-23).
The actual remaining gap verified against current code is smaller: 24
missing entries + the T4A heartbeat gap, not ~30. This entry fixes the
current, re-verified gap, not the audit's original (partially stale) count.

## 3. Fix / remediation

- Added explicit `LOOP_THRESHOLDS` entries for all 24 previously-missing
  loops, sized to ~3x each loop's *real* per-tick cadence (read from its
  own `sleep`/poll code, not its display-name suffix), safety/dispatch
  loops prioritized. Two exceptions, called out inline in the code comment:
  `reconciliation (daily 02:00 UTC)` follows the existing
  `distance_reconciliation`/`stripe_reconcile` convention (2x the *daily*
  cycle, not 3x of its 60s inner poll) since its one real-work tick per day
  can legitimately run long; `t4a_annual_job` gets a 10-minute threshold
  (REL-001's own explicit recommendation, matching `capacity_watchdog`'s
  pattern for a frequently-polling-but-rarely-acting loop) rather than a
  literal 3x-of-a-year.
- Added a `record_heartbeat` call to `t4a_annual_job_loop()`'s outer
  `while True` iteration, called every 60s poll regardless of whether that
  tick ran real issuance work — matching the established
  `reconciliation.py`/`distance_reconciliation.py` fast-inner-poll pattern.
- Added `backend/tests/test_loop_watchdog_thresholds.py`: fails if any
  watchdog-covered loop lacks an explicit threshold, or if one accidentally
  equals the untuned 2h default; pins the 6 priority safety/dispatch loops
  at `<= 5 min`.
- Extended `backend/tests/test_t4a_annual_job.py` with two tests proving
  the loop now heartbeats every tick, including when a tick raises.

**Alternative considered:** add a fast inner poll to each of the affected
loops instead of just tuning the threshold (this is what `reconciliation.py`
and `t4a_annual_job.py` already do). Rejected as the general fix here —
most of the 24 affected loops already heartbeat once per their own natural
tick with no long-running work inside a tick, so a threshold sized to that
real cadence is sufficient and is a pure config change (no loop-logic
change, lower risk, matches CLAUDE.md's additive-first gate). The inner-poll
pattern was still used where actually needed: `t4a_annual_job` already had
the poll structure (correctly) and just needed the heartbeat call wired up.

## 4. Risk & impact on existing functionality

**Blast radius: additive/config-only, narrow, verified below — no loop
business logic changed.**

- `LOOP_THRESHOLDS` is read in exactly one place:
  `loop_monitor.get_loop_status()`. That function has two real callers:
  1. `backend/utils/loop_alert.check_and_alert()`, invoked from
     `core/lifespan.py`'s `_loop_watchdog` task every 5 minutes — **gated
     entirely on `ALERT_WEBHOOK_URL` being set** (`loop_alert.py`: `if not
     webhook_url: return`). **This is unverified in this session** — whether
     `ALERT_WEBHOOK_URL` is actually set in Fly/Railway production is not
     something this session can check (no production secrets access), and
     REL-002 in the same audit lane flags this configuration as UNKNOWN. If
     it is unset, none of these threshold changes change any observable
     production behavior today — they only take effect once/if that webhook
     is configured. This must not be read as "alerting is now fixed."
  2. `backend/worker.py`'s `/health` endpoint — this one **is live** (a real
     Fly health-check target for the dedicated `worker` process) and returns
     503 if any loop it tracks is `"stale"`. However, `worker.py` only ever
     calls `get_loop_status(registered_names=list(tasks))` where `tasks` is
     the worker process's own spawned set — today that's the 3
     `worker_wave1` loops (`push_retry`, `zoho_desk_sync`,
     `driver_onboarding_reminders`) plus `outbox_poller`, none of which this
     change touches (all 4 already had tuned thresholds before this change,
     untouched). **No risk to the worker process's health check.**
  3. `backend/routes/main.py`'s `health_check` also calls
     `get_loop_status`, but that module's own docstring confirms it is dead
     code — never mounted in `server.py`. Confirmed by grep of
     `server.py`'s router-mount block (no `routes.main` import). No
     production traffic reaches it.
  4. The API process's own live `/health` (`server.py:260`) does **not**
     call `loop_monitor` at all (confirmed by grep) — liveness-only,
     unaffected regardless.
- No other module imports `LOOP_THRESHOLDS` or `_DEFAULT_THRESHOLD`
  (grepped `backend/` for both names).
- `t4a_annual_job.py`'s heartbeat addition is a pure addition inside the
  loop's own `while True` — it does not touch `_maybe_run_tick`,
  `_run_issuance`, the Redis lock claim, or any driver-earnings/push/audit
  logic. All 20 existing `test_t4a_annual_job.py` tests still pass
  unmodified.
- **Residual risk not fully closed by this change:** the 10-minute
  `t4a_annual_job` threshold assumes the once-a-year issuance batch
  (`_run_issuance`, sequential per-driver earnings queries + pushes)
  completes in well under 10 minutes at real production driver counts.
  This was not measured against production data volume — see "What was NOT
  verified" below. If it turns out to run longer, the loop would show
  `"stale"` for one tick a year while genuinely healthy (a false alarm, not
  a missed real one) — annoying but not unsafe, and only matters at all
  once `ALERT_WEBHOOK_URL` is confirmed live.

## 5. User-experience effect

None. Backend-only, no rider/driver/corporate-admin/internal-admin-facing
change. Not visible mid-session to anyone. The only possible observable
effect is an internal Slack alert (if `ALERT_WEBHOOK_URL` is configured) or
the dedicated worker process's own `/health` 503 (unaffected, see above) —
neither is user-facing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/loop_monitor.py` | Added 24 explicit `LOOP_THRESHOLDS` entries (previously falling back to the 2h default) | REL-001 — close the coverage gap, safety/dispatch loops first |
| `backend/utils/t4a_annual_job.py` | Added `record_heartbeat` call in the loop's 60s poll iteration | REL-001 Direction C — the loop never heartbeated at all |
| `backend/tests/test_loop_watchdog_thresholds.py` | New file: coverage test + 6 pinned safety/dispatch assertions | Regression guard per ROADMAP N17 |
| `backend/tests/test_t4a_annual_job.py` | Added 2 tests for the new heartbeat call | Prove the T4A fix |

## 7. Before / after

```python
# Before — backend/utils/t4a_annual_job.py
async def t4a_annual_job_loop() -> None:
    """Entry point spawned by lifespan.py. Runs indefinitely."""
    while True:
        try:
            await _maybe_run_tick()
        except Exception:
            logger.error("t4a_annual_job tick failed", exc_info=True)
        await asyncio.sleep(_LOOP_POLL_SECONDS)
```

```python
# After
async def t4a_annual_job_loop() -> None:
    """Entry point spawned by lifespan.py. Runs indefinitely."""
    while True:
        try:
            await _maybe_run_tick()
        except Exception:
            logger.error("t4a_annual_job tick failed", exc_info=True)
        _record_heartbeat(_LOOP_NAME)
        await asyncio.sleep(_LOOP_POLL_SECONDS)
```

```python
# Before — backend/utils/loop_monitor.py (excerpt)
# 24 loops (safety_checkin, offer_expiry_reaper, t4a_annual_job, ... — full
# list in the commit) had no entry here and used _DEFAULT_THRESHOLD (7200s).

# After
"safety_checkin (30s)": 30 * 6,       # 3 min
"offer_expiry_reaper (10s)": 10 * 3,  # 30 s
"t4a_annual_job (yearly Feb 28)": 10 * 60,  # 10 min
# ...(21 more — see commit 8ed6d31 for the full set)
```

## 8. Rollback plan

Pure config values + one additive function call, no migration, no data
written or read differently.

- **Threshold values**: revert `backend/utils/loop_monitor.py` to drop the
  new dict entries (`git revert` of commit `8ed6d31`, or hand-edit back to
  the 2h-default fallback) — safe at any time, since nothing else reads
  these values except the two callers above and reverting only changes
  when/whether an alert fires, never any ride/payment/driver state.
- **T4A heartbeat**: revert `backend/utils/t4a_annual_job.py`'s
  `record_heartbeat` call (commit `716b3da`) — safe at any time; removing it
  only returns the loop to its prior "never_ticked, not flagged unhealthy"
  state, it does not affect `_run_issuance`'s actual tax-slip logic.
- **Tests**: revert or delete `test_loop_watchdog_thresholds.py` and the two
  added `test_t4a_annual_job.py` cases if they ever produce a false
  positive against a legitimately-added, not-yet-tuned loop (unlikely, but
  the coverage test would then be the thing to loosen, not the thresholds).
- No feature flag needed — a `git revert` genuinely is sufficient rollback
  here (unlike money/ride-state changes) because nothing in this diff
  writes to `rides`, `driver_insurance_periods`, wallets, or Stripe; the
  only "state" touched is an in-process, non-persistent heartbeat dict
  (`loop_monitor._heartbeats`, reset on every process restart regardless).

## 9. Verification performed

- [x] Automated tests run (unit): `cd backend && python -m pytest -o addopts="" -q -p no:cacheprovider tests/test_t4a_annual_job.py tests/test_loop_watchdog_thresholds.py tests/test_lifespan_watchdog_coverage.py tests/test_background_loop_registry.py tests/test_loop_monitor_dependency_failure.py tests/test_loop_alert.py` → **68 passed**. Also ran `tests/test_dual_import_symmetry.py` (4 passed) to confirm the new dual-import block in `t4a_annual_job.py` follows the required pattern.
- [x] `ruff check` and `ruff format --check` on all 4 changed/added files → clean (also enforced by the repo's pre-commit hook on each commit).
- [ ] Manual repro steps followed in staging — not applicable/not done; this is a monitoring-config change with no staging-observable effect until `ALERT_WEBHOOK_URL` is confirmed live (see §4).
- [x] Blast-radius grep performed: grepped `backend/` for `LOOP_THRESHOLDS`, `_DEFAULT_THRESHOLD`, `get_loop_status`, and `loop_monitor` imports; read `worker.py`'s `/health` handler and `routes/main.py`'s module docstring in full to confirm which are live vs dead code (see §4).
- [x] Reviewed against relevant CLAUDE.md conventions: dual-import pattern (kept in `t4a_annual_job.py`'s new import block), "do not silently swallow errors" (the existing `except Exception: logger.error(...)` around `_maybe_run_tick()` was left untouched — this change only adds the heartbeat call after it), background-loop replay-safety (no change to the Redis `SET NX EX` claim in `_maybe_run_tick`).
- [ ] Feature-flagged — not applicable; this is a monitoring-threshold-only change with no user-visible or ride/payment/dispatch behavior, additive by construction (CLAUDE.md gate 2/3 — new dict entries and a new function call, no existing behavior mutated).
- [x] Self-reviewed against `.claude/agents/spinr-realtime-reliability-reviewer.md` (the named agent was unavailable via the Agent tool in this session — see below); no findings requiring a fix.

## 10. What was NOT verified

- **Whether `ALERT_WEBHOOK_URL` is actually set in Fly/Railway production is
  NOT verified.** This is the single biggest caveat: if it is unset, none of
  these threshold changes have any live effect — `loop_alert.check_and_alert`
  no-ops entirely without it. This is the same open question REL-002 in the
  source audit lane already flags as UNKNOWN; this change does not resolve
  it, and alerts only reach a human once that variable is confirmed
  configured.
- **The `t4a_annual_job` 10-minute threshold was not exercised against a
  real annual batch run at production driver counts** — only against mocked
  unit tests of the heartbeat call itself. If `_run_issuance` genuinely
  takes longer than 10 minutes at scale, the one tick a year that does real
  work could show a false "stale" reading (see §4).
- **Not tested against a live Redis or live Supabase** — all tests use
  mocks (`unittest.mock`), per this repo's own unit-test convention; no
  integration-tier or RLS-tier test was added or run for this change (none
  is needed — nothing here touches a DB table or RLS policy).
- **The full backend test suite was not run** (only the loop/watchdog/T4A
  subset relevant to this diff, per this task's stated scope) — no
  production build step applies here (this is a Python backend-only change,
  not `admin-dashboard`/`rider-app`/`driver-app`).
- **No manual/staging verification of an actual Slack alert firing** — the
  existing `test_lifespan_watchdog_coverage.py`
  `TestWatchdogFlagsAHungNewlyAddedLoop` test (not modified by this change)
  already proves the alert-posting mechanism works end-to-end for a
  representative loop; this change was verified against that same proven
  mechanism, not re-proven from scratch.

## Addendum: progress heartbeats after reviewer pass

The `spinr-realtime-reliability-reviewer` pass found no blockers. It raised two SHOULD-FIX items, and both are fixed in this PR:

| File | What changed | Why |
|---|---|---|
| `backend/utils/t4a_annual_job.py` | Calls `_record_heartbeat(_LOOP_NAME)` once per driver in both `_run_issuance` loops. | The loop heartbeat fired only after the whole annual batch. A long but healthy batch would therefore read as stale against the 10-minute threshold, on the one day the job matters. |
| `backend/utils/auto_payout.py` | Calls `_record_heartbeat("auto_payout (1h, Sundays)")` once per driver in `run_weekly_auto_payout`. | Same pattern: a long Sunday batch could exceed the 3 h threshold. **Money file, but no payout, Stripe or eligibility logic changed.** The only addition is an in-process heartbeat timestamp (a dict write under a lock). |
| `backend/utils/loop_monitor.py` | Corrected both threshold comments. | The earlier auto_payout comment wrongly said its heartbeat was independent of the batch runtime. |

**Before/after (auto_payout):**
```python
# before
for driver in drivers:
    driver_id = driver["id"]
# after
for driver in drivers:
    _record_heartbeat("auto_payout (1h, Sundays)")  # progress only
    driver_id = driver["id"]
```

**Verification:** 132 tests passed. They cover the T4A job, the threshold coverage, the watchdog coverage, the loop monitor, loop alerts, and every `tests/*auto_payout*` file. `ruff check` and `ruff format --check` are clean.

**Not verified:** a real Sunday payout batch, or a real annual T4A batch, at production driver counts.

**Out of scope, noted for follow-up:** `auto_payout`'s leader lock TTL is `interval * 0.85` (about 51 min). A batch running longer than that could let a second replica take the lock. Payout idempotency should prevent double transfers, but this was not verified here.
