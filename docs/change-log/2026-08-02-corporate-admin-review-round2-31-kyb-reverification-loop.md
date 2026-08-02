# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "automated KYB re-verification" — background loop slice |

## 1. Issue / gap identified

The round2-30 schema exists but nothing scans for stale KYB approvals
yet.

## 2. Root cause

Never built — see round2-30 for full background.

## 3. Fix / remediation

New `backend/utils/kyb_reverification.py`, invoking the
`spinr-background-loop` skill and mirroring `utils/corporate_low_balance.py`'s
structure line-for-line (same heartbeat/metrics/jittered-sleep shape as
every other loop in `core/lifespan.py`):

- `run_kyb_reverification_tick()` — fetches active, KYB-approved
  companies older than the configured threshold (default 12 months,
  `corporate_kyb_reverify_after_months` in `app_settings`), skips any
  company already flagged within the last 30 days (cooldown-in-Python,
  same pattern as low-balance's 12h email cooldown — no new DB filter
  logic needed), and for each newly-stale company: writes an `info` log
  line, increments `spinr_corporate_kyb_reverification_due_total`, and
  stamps the claim flag. **Never touches `corporate_accounts.status` or
  any KYB decision field** — the only write this tick performs is the
  claim-flag stamp, directly matching the product decision (visibility
  only, no auto-reverification, no status change).
- Respects a kill switch (`corporate_kyb_reverification_enabled`,
  default `true`) checked first, short-circuiting before any DB query —
  same convention as the scheduled-dispatcher kill switch from earlier
  this round's Track A work.
- `kyb_reverification_loop()` — 24h tick (±10% jitter), wired into
  `core/lifespan.py` immediately after `allowance_reset_loop`, wrapped in
  the same `try/except` import-failure isolation every other loop
  registration uses.
- Two new `SettingsUpdateRequest` fields
  (`corporate_kyb_reverification_enabled`,
  `corporate_kyb_reverify_after_months`, `ge=1, le=60`) so both are
  configurable without redeploy, matching this codebase's established
  `app_settings` pattern.
- Updated `CLAUDE.md`'s own lifespan.py loop count/list (17 → 18 loops,
  added "corporate KYB re-verification reminder" to the enumerated list)
  — this file is checked into the repo and had gone stale the moment this
  loop was added; left uncorrected it would mislead the next session
  reading it.

New `backend/tests/test_kyb_reverification.py`, mirroring
`test_corporate_low_balance.py`'s pattern. 8 tests: happy-path flag +
metric emission, an explicit assertion that **no status-mutating call
exists anywhere to make** (the visibility-only guarantee, made
structurally checkable, not just asserted in a comment), within-cooldown
skip, re-flag after cooldown elapses, kill-switch short-circuit before
any query, custom threshold pass-through, and per-company failure
isolation (one company's flag failure doesn't block another's).

## 4. Risk & impact on existing functionality

- **Blast radius: two new files + one new registration block in
  `lifespan.py` + two new optional settings fields.** No existing loop,
  registration, or settings field was touched — confirmed by diff, the
  new `_spawn(...)` call was inserted after `allowance_reset_loop`'s
  block without altering it.
- Every other background loop's registration, kill-switch behavior, and
  metric names are unchanged.
- This loop's own worst-case failure mode (an exception mid-tick) is
  caught by the outer `while True` loop's `try/except`, logged, and
  metric-flagged (`spinr_bgloop_errors_total`) — it retries next tick 24h
  later, same resilience posture as every other loop in this file.
- **No new external dependency** — no third-party KYB provider
  integration, consistent with the product decision explicitly ruling
  that scope out.

## 5. User-experience effect

None yet — no admin-facing surface reads this data in this commit
(round2-32/33 are the API + UI slices). An admin watching Prometheus/logs
directly could already see the new metric and log lines once this loop
starts running.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/kyb_reverification.py` | New file: tick function + loop | Scan for stale KYB approvals, visibility-only |
| `backend/core/lifespan.py` | New `_spawn("kyb_reverification (24h)", ...)` registration | Wire the loop into the standard startup sequence |
| `backend/routes/admin/settings.py` | 2 new optional settings fields | Kill switch + configurable threshold, no redeploy needed |
| `backend/tests/test_kyb_reverification.py` | New file: 8 tests | Cover the tick's every branch, especially the visibility-only guarantee |
| `CLAUDE.md` | Loop count 17→18, added this loop to the enumerated list | The checked-in doc had gone stale the moment this loop was registered |

## 7. Rollback plan

`git revert` the commit. The loop's own kill switch
(`corporate_kyb_reverification_enabled`) is also a live, no-deploy
rollback lever if a revert isn't immediate — flip it off and the loop
becomes a no-op every tick. No data written by this commit that a
revert needs to clean up (the loop hasn't run yet — it starts on next
deploy).

## 8. Verification performed

- [x] `ast.parse` syntax check on all four modified/new Python files —
      clean.
- [x] Invoked the `spinr-background-loop` skill before writing any code,
      confirming the replay-safety contract (claim-flag column) this
      loop follows.
- [x] Compared every structural element (heartbeat call, metric names,
      jittered sleep, `try/except` isolation) against
      `utils/corporate_low_balance.py` line-by-line rather than
      reinventing the loop shape.
- [x] Confirmed `_metric_inc`'s actual signature (`by=`, not `amount=`)
      by reading `utils/metrics.py` before calling it — caught a
      parameter-name mismatch before it became a runtime `TypeError`.
- [x] Manually verified the "never touches status" claim by reading
      `mark_kyb_reverify_flagged`'s implementation (round2-30) — it only
      ever writes `kyb_reverify_flagged_at`, nothing else.
- [x] Did **not** run `pytest` for either Python file — per this round's
      explicit "don't run tests until everything is developed"
      instruction; deferred to the single end-of-round pass.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, plus the kill switch is
      an independent, no-deploy lever
- [x] Blast radius is stated, not assumed — confirmed via diff that no
      existing loop registration or settings field was touched
- [x] No silent behavior change to a working flow — new, additive loop;
      nothing existing depends on or is affected by it
- [x] Any new background loop must be replay-safe (CLAUDE.md) — the
      claim-flag pattern is the same one already proven for
      `low_balance_notified_at`

## What was NOT verified

Did not run `pytest`, and did not start the backend server to confirm
the loop actually registers and ticks correctly against a live
`core/lifespan.py` startup sequence — no server run in this session. The
"never touches status" guarantee is verified by reading the called
function's implementation, not by an integration test that would catch a
future accidental regression in `mark_kyb_reverify_flagged` itself (that
function's own correctness is round2-30's test surface, not this
commit's).
