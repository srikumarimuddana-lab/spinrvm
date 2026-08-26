# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (local commit, not yet pushed/PR'd — see commit SHA in final report) |
| Related issue or gap ID | `docs/audit/2026-08-19-decision-writeups.md` section 9 ("Confirm whether the silently-failed nightly `purge_pii_retention()` job ever produced a loud, actionable alert") |

## 1. Issue / gap identified

`backend/utils/retention_purge.py`'s daily PII/DSAR retention-purge loop had no Sentry capture
anywhere, its `_metric_inc(...)` error counter was a documented no-op stub, and — critically —
`_record_heartbeat("retention_purge (24h)")` fired unconditionally *after* the tick's
try/except, so a failing tick still refreshed the loop watchdog's heartbeat. A repeat failure of
this regulatory (PIPEDA) purge job would have produced only one ERROR-level log line, no page,
and the watchdog reporting the loop healthy.

## 2. Root cause

The loop wrapper (`retention_purge_loop`) followed an older, minimal error-handling pattern
(`logger.exception` + a stubbed metric increment) that predates the Sentry `fatal`-capture +
audit-row escalation pattern later added to the sibling `retention_guard_monitor.py` (A35,
2026-08-17). The heartbeat call's placement outside the try/except was the specific mechanism
that let this go undetected: the watchdog (`core/lifespan.py` → `loop_monitor.py`) only measures
"did this loop name's heartbeat get touched recently," not "did the last tick succeed" — so an
individual run failing while the loop kept ticking on its 24h schedule was structurally invisible
to it. This exact mechanism previously let the Step D/Step F `purge_pii_retention()` bugs run
undetected until an unrelated audit found them (`docs/change-log/2026-08-17-c22-...md`).

## 3. Fix / remediation

- Added `_escalate_tick_failure(exc)` to `retention_purge.py`, mirroring
  `retention_guard_monitor.py`'s `_escalate()` exactly: a CRITICAL log line, then a
  `sentry_sdk.capture_message(..., level="fatal", tags={"spinr_alert": ..., "domain": "admin",
  "surface": "backend"}, contexts={...})` call, wrapped in its own try/except so a Sentry SDK
  failure can never break the loop. Message/context carry only the exception's *type name* —
  never row data or any PII, consistent with every other log line already in this file and with
  CLAUDE.md's PIPEDA logging rules.
- Called `_escalate_tick_failure(exc)` from `retention_purge_loop`'s existing `except Exception`
  branch, alongside the pre-existing `logger.exception` and `_metric_inc(...)` calls — neither
  removed, both kept.
- Moved `_record_heartbeat("retention_purge (24h)")` from unconditionally after the try/except to
  inside the `try` block, immediately after a successful `_tick()` call. A leader-lock skip
  (another replica already ran) still counts as success inside `_tick()` and still reaches this
  line, so it still heartbeats — only an actual exception now withholds the heartbeat.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `retention_purge_loop`'s own heartbeat/alerting, additive
  otherwise.** Grepped `"retention_purge"` across the repo (code + docs): the heartbeat name
  `"retention_purge (24h)"` is written only by this loop and read only by two places —
  `backend/core/lifespan.py` (the `_WATCHDOG_LOOP_NAMES` list, which just re-lists loop names for
  the watchdog scan — no logic keyed to this specific name) and
  `backend/utils/loop_monitor.py`'s `LOOP_THRESHOLDS` dict (a 48h staleness threshold, generic
  per-loop-name lookup). No other loop, route, or service reads or writes this heartbeat key or
  any metric name introduced here. `run_retention_purge_tick()`, `_tick()`, and the SQL RPC calls
  themselves (`purge_pii_retention`, `purge_trip_route_geometry`) are **untouched** — this change
  does not alter what the purge does, its idempotency, or its leader-lock replay-safety guarantee
  (per the `spinr-background-loop` skill's contract, confirmed explicitly below).
- **Moving the heartbeat is observability-only.** It changes only when the watchdog is told "this
  loop is healthy" — it does not change any DB write, does not change retry/replay behavior
  (there is still exactly one attempt per scheduled 24h tick; a failed tick is not retried
  early), and does not change the leader-lock semantics (`_tick()`'s `redis_set_nx` skip path is
  unaffected and still reaches the heartbeat as a success).
- **New failure-mode-detection risk, working as intended, is a behavior change worth naming
  explicitly:** if `purge_pii_retention()` starts failing every night (e.g. a schema drift, a
  broken RPC), the heartbeat will now go stale after ~48h (2× the 24h interval, per
  `LOOP_THRESHOLDS["retention_purge (24h)"]`) and the watchdog will start reporting this loop
  unhealthy — plus a Sentry `fatal` page on every failed tick. This is the intended fix, not a
  regression, but it means on-call should expect to see this loop flagged if it is *already*
  silently failing in production today (unknown — no visibility existed before this change to
  confirm either way).
- **No other consumer of `retention_purge_loop`, `_tick`, or `run_retention_purge_tick`** —
  confirmed via the existing blast-radius greps recorded in prior change-log entries for this
  file (`2026-08-03-a1c-found-not-fixed-bugfixes.md`, `2026-08-17-a38-...md`,
  `2026-08-17-c22-...md`) plus a fresh grep for `retention_purge` in this session — all hits are
  this file, its own tests, `core/lifespan.py`'s spawn/watchdog registration, `loop_monitor.py`'s
  threshold table, and documentation/change-log references. No route or admin endpoint calls into
  this module.
- **`sentry_sdk` import failure is handled the same way the sibling module handles it**: wrapped
  in its own try/except that only logs at `debug` — a missing/misconfigured Sentry SDK cannot
  break the purge loop.

## 5. User-experience effect

None. This is a backend-only observability change to an unattended nightly background loop. No
rider, driver, corporate-admin, or internal-admin-facing UI, copy, or API response changes. The
only "user" of this change is on-call/engineering, who will now receive a Sentry page (instead of
silence) if the job fails, and will see the loop correctly flagged unhealthy on `/health` /
Sentry after ~48h of repeated failure instead of never.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/retention_purge.py` | Added `_escalate_tick_failure()` (CRITICAL log + Sentry `fatal` capture, `domain="admin"`/`surface="backend"` tags, exception-type-only context); moved `_record_heartbeat("retention_purge (24h)")` into the success path of `retention_purge_loop` | Close the alerting gap identified in decision-writeup section 9 — a repeat tick failure must page, and must not refresh the watchdog heartbeat |
| `backend/tests/test_p3_loop_jitter_metrics.py` | Added tests: heartbeat recorded on success, heartbeat NOT recorded on failure, Sentry capture fires with `domain="admin"`/`level="fatal"` on failure, Sentry capture context carries only the exception type (no PII) | Regression coverage for the new alerting/heartbeat behavior |
| `docs/audit/2026-08-19-decision-writeups.md` | Marked section 9's recommendation as implemented, with a commit reference | Close out the decision-log item per the task |
| `docs/change-log/2026-08-21-purge-pii-retention-alerting-fix.md` | New file (this document) | Mandatory Change Impact & Risk Log for a live-tested background-loop change (CLAUDE.md) |

## 7. Before / after

```python
# Before
async def retention_purge_loop() -> None:
    while True:
        t0 = asyncio.get_event_loop().time()
        try:
            await _tick()
            _metric_gauge(
                "spinr_bgloop_duration_ms",
                (asyncio.get_event_loop().time() - t0) * 1000,
                {"loop": "retention_purge"},
            )
        except Exception:
            logger.exception("retention_purge_loop: tick raised")
            _metric_inc("spinr_bgloop_errors_total", {"loop": "retention_purge"})
        _record_heartbeat("retention_purge (24h)")  # fires even on failure
        await asyncio.sleep(INTERVAL_SECONDS * (0.9 + random.random() * 0.2))
```

```python
# After
async def retention_purge_loop() -> None:
    while True:
        t0 = asyncio.get_event_loop().time()
        try:
            await _tick()
            _metric_gauge(
                "spinr_bgloop_duration_ms",
                (asyncio.get_event_loop().time() - t0) * 1000,
                {"loop": "retention_purge"},
            )
            _record_heartbeat("retention_purge (24h)")  # success-path only
        except Exception as exc:
            logger.exception("retention_purge_loop: tick raised")
            _metric_inc("spinr_bgloop_errors_total", {"loop": "retention_purge"})
            _escalate_tick_failure(exc)  # CRITICAL log + Sentry fatal capture
        await asyncio.sleep(INTERVAL_SECONDS * (0.9 + random.random() * 0.2))
```

## 8. Rollback plan

Pure code change, no migration, no feature flag, no data mutation — `git revert` of the single
commit is a complete and sufficient rollback. Reverting restores the prior (silent-failure)
behavior exactly; nothing in production data, Stripe, or ride/wallet state is touched by this
change, so no data-level remediation is needed either way.

## 9. Verification performed

- [x] Automated tests: added 4 new unit tests to `backend/tests/test_p3_loop_jitter_metrics.py`
  (heartbeat-on-success, heartbeat-withheld-on-failure, Sentry-capture-on-failure,
  Sentry-context-carries-no-PII), run alongside the existing 4 tests in that file covering this
  loop's jitter/metrics behavior, plus the full `backend/tests/test_retention_purge.py`,
  `backend/tests/test_retention_purge_coverage.py`, `backend/tests/test_retention_guard_monitor.py`,
  and `backend/tests/test_lifespan_watchdog_coverage.py` suites for regression. See the session's
  final report for the exact pass/fail counts and any environment caveats.
- [x] `ruff check` run on the two changed Python files.
- [x] Blast-radius grep performed: `retention_purge` across the full repo (see section 4).
- [x] Reviewed against CLAUDE.md conventions: Observability Conventions ("User-visible errors →
  Sentry (with domain tag) + error log" — followed; "Never `logger.warning(...)` and continue on
  a DB/auth/payment error" — unaffected, this file already used `logger.exception`/`logger.error`
  and still does), PIPEDA logging rules (no raw PII in the new log/Sentry calls), and the
  `spinr-background-loop` skill's replay-safety contract (confirmed below — unaffected, this
  change is observability-only).
- [ ] Not exercised against a live/staging Supabase or a live Sentry project — this is a unit-test
  verification only (see below).

## What was NOT verified

- Not exercised against a real Sentry DSN/project — the Sentry call is verified by mocking
  `sentry_sdk.capture_message` at the call site (matching this repo's existing convention, e.g.
  `test_stale_in_progress_ride_alerter.py`), not by confirming an event actually lands in Sentry's
  dashboard.
- Not run against a live/staging Supabase instance — `run_retention_purge_tick`/`_tick` are
  exercised only via the existing mocked-Supabase test suite; the underlying SQL functions
  (`purge_pii_retention`, `purge_trip_route_geometry`) are unmodified by this change and were
  already covered by migration-level tests elsewhere.
- No production build step applies — this is a Python backend-only change (no
  `admin-dashboard`/`rider-app`/`driver-app` code touched), so the `npm run build` requirement in
  CLAUDE.md's Verification-performed field is not applicable here.
- Whether `purge_pii_retention()` is *currently* failing nightly in production is unknown and out
  of scope for this fix — this change makes that state visible going forward; it does not itself
  confirm or rule out an active incident.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single `git revert`, no data-level follow-up needed)
- [x] Blast radius is stated, not assumed (heartbeat key + metric names grepped repo-wide; no
  other reader/writer found besides the watchdog itself)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (this
  change has no user-facing surface; the UX field above states that explicitly)
