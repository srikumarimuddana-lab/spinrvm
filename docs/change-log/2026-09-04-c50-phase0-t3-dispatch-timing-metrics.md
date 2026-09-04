# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (session_01Sspqro7zzjKdTbUh6D61wQ) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | `claude/c50-phase0-t3-dispatch-timing-metrics` (see PR) |
| Related issue or gap ID | ACTION_ITEMS.md C50, Phase 0 T3 (per `docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md`) |

## 1. Issue / gap identified

C50's Phase 0 T3 calls for additive per-phase timing instrumentation on the
dispatch claim path (`run_sync` queue-wait/exec-time, per-phase
`spinr_dispatch_attempt_duration_ms`, and a per-attempt DB-call count), so
gate G3/G4 can later compare PostgREST vs. a direct Supavisor pool. **This
task found the instrumentation itself already present and tested on `main`**
— it was built and reviewed across several earlier sessions/commits (see
§2). The actual gap this entry closes is two documentation loose ends the
plan's T3 file list named but that were still missing: `loadtest/README.md`
had no mention of the new metrics for the eventual T5 load-test run, and no
dedicated `docs/change-log/` entry existed for T3 specifically (the two
change-log entries that do exist cover a metrics/RLS CI batch and the later
Phase 2 review-fixes batch, not a standalone T3 record pointing at the exact
plan task).

## 2. Root cause

Not a bug — a sequencing/documentation gap. The T3 instrumentation shipped in
prior sessions via (confirmed by `git log`):
- `49105acb5` — `feat(metrics): add run_sync queue-wait/exec-time histograms + db call counter`
- `59ba299f4` — `fix(metrics): count DB calls made in gather children (C50 T3)`
- `ef3842516` — `fix(metrics): record measured cost of run_sync instrumentation (finding 5)`
- `9379917dd` — `test(metrics): add coverage for T3 dispatch timing instrumentation`
- plus the per-phase `spinr_dispatch_attempt_duration_ms` wraps already present in `backend/routes/rides/matching.py` (phases `candidate_read`, `rank`, `claim`, `offer_insert`, `insurance`, `notify` — exactly the plan's named breakdown) and `backend/tests/test_dispatch_metrics.py`'s T3 test block.

`loadtest/README.md` and a T3-specific change-log entry were never added
alongside that work, so this task (asked to "implement T3") verified the
existing implementation against the plan's exact spec line-by-line rather
than re-implementing it, and closed the two remaining file-list items.

## 3. Fix / remediation

- **Verified** (no code change) that `backend/repositories/_base.py`'s
  `run_sync` records `spinr_db_run_sync_queue_wait_ms` (submit → thread
  start) and `spinr_db_run_sync_exec_ms` (thread start → return) via
  `utils/metrics.observe`, using `DEFAULT_MS_BUCKETS`, and that a
  `contextvars`-backed counter (`reset_db_call_count` /
  `_incr_db_call_count` / `get_db_call_count`) tracks DB calls per logical
  unit of work, additive across `asyncio.gather()` children (mutable
  one-element-list container, not a plain `ContextVar[int]` — see the
  module comment at `_base.py:284-306` for why the plain-int version would
  under-count).
- **Verified** that `backend/routes/rides/matching.py`'s
  `_match_driver_to_ride_attempt` wraps exactly the plan's six named phases
  (`candidate_read`, `rank`, `claim`, `offer_insert`, `insurance`, `notify`)
  in `spinr_dispatch_attempt_duration_ms{phase=...}` via `utils.metrics.time_ms`,
  resets the DB-call counter at the start of an attempt
  (`_reset_db_call_count()`), and observes
  `spinr_dispatch_attempt_db_calls` at the end.
- **Verified** `backend/tests/test_dispatch_metrics.py` already covers both
  the success path (`test_match_driver_to_ride_records_phase_timings_and_db_calls`)
  and failure/early-exit paths (`test_run_sync_records_exec_time_on_exception_path`,
  `test_match_driver_to_ride_records_db_calls_on_early_no_drivers_return`),
  plus the gather-additivity and cross-attempt isolation regressions
  (`test_db_call_counter_includes_calls_made_in_gather_children`,
  `test_db_call_counter_isolates_concurrent_dispatch_attempts`). No test
  changes were needed to meet this task's stated verification bar.
- **Added** (the actual diff in this PR):
  - `loadtest/README.md` — one bullet in the "watch the backend's own
    telemetry" list naming the four T3 metrics, so the next person running
    T5 (600-user staging ramp) knows to scrape and record them alongside the
    existing offer→accept histogram.
  - This change-log entry.
- Grafana panel (optional per the plan's T3 "What" list) — not added; the
  plan marks it optional and no Grafana access/verification was available in
  this task's scope.

## 4. Risk & impact on existing functionality

- **Blast radius: none — no production code changed.** This PR touches only
  `loadtest/README.md` (a doc read by humans running the load-test harness,
  not executed) and a new `docs/change-log/` file. `git diff --stat` against
  `main` confirms no `.py` file is modified.
- The instrumentation code itself (`_base.py`'s `run_sync`,
  `matching.py`'s phase wraps) was **not touched** by this task — it was
  already live on `main`. For completeness, `run_sync` is called from 318
  call sites outside `_base.py` across nearly every DB-touching backend
  module (`db_supabase.py`, all of `repositories/*_repo.py`, most of
  `routes/**`, `utils/*` background loops) — grepped via
  `grep -rn "run_sync" backend/ --include=*.py | grep -v /tests/`. None of
  those call sites are affected by this PR since nothing in `run_sync`
  changed here; this note documents the blast radius of the *existing*
  instrumentation (already accepted risk from the earlier commits above),
  not a new risk this PR introduces.
- **Instrumentation overhead** (restated per this task's ask, not
  re-measured — no new timing calls were added by this PR): each
  `time.monotonic()` call costs on the order of 50-100ns; `run_sync` adds
  two such deltas plus one histogram `observe()` (a dict lookup + list
  append under a module-level `threading.Lock`) per DB call, and
  `_match_driver_to_ride_attempt` adds six `time_ms` context managers per
  attempt. This is several orders of magnitude below the < 2s dispatch P95
  SLA and was already accepted when the instrumentation shipped
  (`ef3842516` — "record measured cost of run_sync instrumentation" — measured
  this directly in an earlier session; not re-run here).

## 5. User-experience effect

None. Backend-only, additive observability; no API response shape, ride
state, or UI changed. Not visible mid-session to any rider, driver, or admin.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `loadtest/README.md` | Added one bullet under "watch the backend's own telemetry" naming the T3 per-phase/queue-wait/exec/db-calls metrics | So the next T5 (600-user staging ramp) run records them alongside the existing offer→accept histogram, per the plan's T5 "Capture" list |
| `docs/change-log/2026-09-04-c50-phase0-t3-dispatch-timing-metrics.md` | New — this entry | Mandatory per `CLAUDE.md` for a dispatch-surface-adjacent change; closes the T3 file-list item that had no standalone record |

No `backend/*.py` file was modified by this task — the instrumentation code
itself already existed on `main` (see §2 for the originating commits).

## 7. Before / after

Not applicable — no behavior-changing diff. Both changes are additive
documentation (a README bullet, a new change-log file).

## 8. Rollback plan

`git revert` this commit. Since no production code path changed, there is no
data-level remediation needed — reverting just removes the README bullet and
this doc.

## 9. Verification performed

- [x] Automated tests run: `cd backend && python3 -m pytest
  tests/test_dispatch_metrics.py tests/ -k "matching or dispatch" --no-cov -q`
  → **490 passed, 4 skipped, 2 failed.** All `test_dispatch_metrics.py` T3
  cases pass. The 2 failures
  (`test_dispatch_db_errors.py::test_postgrest_claim_loop_releases_prior_claims_and_reraises`,
  `test_dispatch_match_attempt_branches.py::test_claim_loop_exception_releases_earlier_claims_and_reraises`)
  are **pre-existing on `main`, unrelated to this PR** — confirmed via
  `git diff --stat` showing zero `.py` files changed by this branch before
  running the suite. Root cause: `resolve_matching_config` now returns a
  6-tuple (`de8695359`, "wire geo-provider framework, add configurable
  candidate pool", merged same day) but these two tests' mocks still return
  a 5-tuple, so `_match_driver_to_ride_attempt`'s unpack raises
  `ValueError: not enough values to unpack (expected 6, got 5)` before the
  claim loop under test ever runs. Out of this task's scope (T3 doc-only);
  not fixed here — flagged separately (see note below).
- [x] `ruff check .` from `backend/` — pre-existing 40 findings across the
  tree (`S310` audit findings in `utils/subprocessor_audit.py`, others),
  none in the two files this PR touches (`ruff` does not lint Markdown).
  Not introduced by this PR.
- [ ] Manual repro in staging — not applicable; no runtime behavior to repro.
- [x] Blast-radius grep performed: `grep -rn "run_sync" backend/ --include=*.py
  | grep -v /tests/` → 318 matches outside `_base.py`, listed in §4; none
  affected since `run_sync` itself is unchanged by this PR.
- [x] Reviewed against `CLAUDE.md` Observability Conventions — metric names
  already follow `spinr_<domain>_<metric>_<unit>` (verified, not renamed).
- [x] Feature-flagged — not applicable; pure additive metrics/docs, no
  user-visible or behavior-changing surface to flag.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data
  remediation needed).
- [x] Blast radius is stated, not assumed: zero production code changed;
  documented the pre-existing 318-call-site fan-out of `run_sync` for
  completeness even though this PR doesn't touch it.
- [x] No silent behavior change — none occurred. The 2 pre-existing test
  failures found during verification are called out explicitly above, not
  silently left for a reviewer to trip over, and are out of scope for this
  PR (not a T3 regression).

## Explicitly out of scope (per the task that produced this PR)

- **T4-T7** (staging stand-up, the 600-user Locust run, pooler-facts
  checklist, ADR-011 Go/No-Go) remain blocked/unattempted — T4 needs human
  ops actions (Fly app, throwaway Supabase project, GitHub secrets), T5
  depends on T4, T6 needs Kiran's Supabase dashboard access, T7 needs T2/T5/T6
  plus Kiran as decider. This PR does not advance, and must not be read as
  advancing, the direct-pool migration Go/No-Go decision itself.
- The `resolve_matching_config` 6-vs-5-tuple test mock drift found during
  verification (see §9) — a real, narrow, pre-existing bug unrelated to T3,
  suggested as a separate follow-up task rather than folded into this PR.
