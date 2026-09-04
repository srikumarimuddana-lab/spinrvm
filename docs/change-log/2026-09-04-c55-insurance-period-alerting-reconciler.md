# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (claude-sonnet-5) on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | ACTION_ITEMS.md C55; WS-12 §3 (`docs/CRITICAL_BUGS_IMPLEMENTATION_PLAN.md`); found in `docs/audit/2026-09-02-t2-dispatch-reviewer-retro.md` §3 |

## 1. Issue / gap identified

`record_period_transition`'s deliberate swallow-on-failure design (a dropped
driver-insurance-period audit write must never block the ride state
machine) had two unclosed safety-net threads: (a) the metric it increments
on failure, `spinr_insurance_period_write_failed_total`, has been documented
in `docs/runbooks/deploy-migration-64-65.md` as "alert immediately if
non-zero" since migration 64/65, but no rule for it existed in the tracked
`metrics-agent/grafana/alert-rules.yaml`; (b) WS-12 §3 specified a
`utils/insurance_period_reconciler.py` background loop to self-heal a
missed period-open every 10 minutes, but only the Period-3-specific
`utils/stale_p3_closer.py` was ever built — no Period-1/2 equivalent
existed, so a dropped write had no automated correction path.

## 2. Root cause

Both gaps are the same class of omission: the write-failure design (a
correct, documented trade-off — see the audit doc §3 and the module
docstring in `insurance_periods.py`) was shipped without its two companion
pieces (alert + self-heal) ever landing. Neither was a regression; they
were simply never built.

## 3. Fix / remediation

1. Added a third rule to `metrics-agent/grafana/alert-rules.yaml`
   (`spinr-insurance-period-write-failed`), structurally mirroring the
   existing `spinr-dispatch-latency-p95-breach` rule (same `apiVersion`,
   `groups`/`rules` shape, `severity`/`domain` label convention,
   `relativeTimeRange`+`threshold` expression pattern). Condition:
   `sum(increase(spinr_insurance_period_write_failed_total[5m])) > 0`, no
   `for` debounce (`for: 0m`) since the runbook says "alert immediately."
2. Added `backend/utils/insurance_period_reconciler.py`, a new background
   loop (registered in `backend/core/lifespan.py`) that runs every 10
   minutes and derives the *expected* insurance period for two driver
   populations, straight from CLAUDE.md's period table: (a) a driver on a
   pending `ride_offers` row, or linked via `rides.driver_id` to a ride in
   `driver_assigned`/`driver_accepted`/`driver_arrived` → Period 2, tied to
   that ride; a driver linked to an `in_progress` ride → Period 3; (b) a
   driver with `is_online = true` and neither of the above → Period 1. It
   compares each against the driver's currently-open
   `driver_insurance_periods` row and calls the existing
   `record_period_transition` RPC to correct a missing or wrong
   period/ride_id — always for the Period-2/3 (active-ride) case, since
   that direction only ever adds/repairs audit coverage. Downgrading an
   *online-idle* driver's already-open Period 2/3 row down to Period 1 is
   gated behind a new app_settings flag,
   `insurance_period_reconciler_downgrade_enabled` (default off): always
   logged (`logger.warning`) + metric-tracked
   (`spinr_insurance_period_reconciler_downgrade_candidate_total`), but only
   actually written when the flag is on. This mirrors
   `stale_p3_closer.py`'s `stale_p3_autoclose_enabled` alert-first pattern,
   and exists because this loop's own ride/offer scan is the only thing
   standing between "driver looks idle" and "driver is actually idle" — an
   incomplete scan (batch limit, transient read miss) would otherwise be
   able to close a live commercial-coverage window under an active driver,
   which is a materially worse failure than a missed audit row.
3. Registered the loop in `_WATCHDOG_LOOP_NAMES` per the
   `spinr-background-loop` skill's recipe; updated
   `test_lifespan_watchdog_coverage.py`'s pinned spawn/watch counts
   (41→42 spawned incl. `loop_watchdog`, 40→41 watched) and its docstring.
   Bumped `CLAUDE.md`'s "40 background asyncio loops" / "26 hold... Redis
   leader locks" references to 41/27.
4. Marked WS-12 §3/§4 done in `docs/CRITICAL_BUGS_IMPLEMENTATION_PLAN.md`
   and closed ACTION_ITEMS.md C55, both noting two deliberate deviations
   from the original spec (no `reconciled=true` marker column — see §4
   below; the online-idle downgrade path is flag-gated, not unconditional
   like the spec's other two bullets).
5. Added `backend/tests/test_insurance_period_reconciler.py` (12 tests).

## 4. Risk & impact on existing functionality

**Blast radius of `record_period_transition` (the function this loop now
also calls):** grepped every caller repo-wide (excluding tests):
`backend/services/corporate_member_offboarding_service.py`,
`backend/services/corporate_suspension_service.py`,
`backend/utils/stale_intent_reconciler.py`, `backend/utils/spinr_pass.py`,
`backend/utils/insurance_periods.py` (the function's own module),
`backend/routes/users.py`, `backend/routes/admin/rides.py`,
`backend/routes/drivers/ride_flow.py`, `ride_complete.py`,
`subscriptions.py`, `ride_cancel.py`, `profile.py`, `status.py`,
`backend/routes/rides/matching.py`, `cancellation.py`, `lifecycle.py`, plus
the new `insurance_period_reconciler.py` itself. None of these are called
BY the reconciler or call it — the reconciler is a new, independent writer
into the same table via the same shared RPC, exactly the pattern
`stale_intent_reconciler.py` (Period 0/1) and `stale_p3_closer.py` (Period
3 close) already establish. `record_period_transition` itself is unchanged
by this PR — no signature or behavior change, so every existing caller is
unaffected.

**Shared state:** `driver_insurance_periods` (migration 64's append-only
table, partial-unique index on `driver_id WHERE ended_at IS NULL`) and the
`record_insurance_period_transition` RPC (migration 253) are both read and
written by this new loop, but through the same sanctioned path every other
caller uses — no new SQL, no direct table mutation. The reconciler itself
never issues an `UPDATE`/`DELETE` against `driver_insurance_periods`; it
only ever calls the RPC, which enforces append-only + the partial unique
index server-side. Concurrent-write safety: the RPC's own `noop`/`race`
handling (migration 253) already covers the case where the reconciler and
a real-time caller (e.g. `matching.py`'s claim/offer loop) race on the same
driver — whichever call the DB serializes second gets `noop` or `race`,
never corrupts the row.

**Comparison against sibling loops for replay-safety** (per
`spinr-background-loop` skill contract — "atomic DB claim" / "claim flag" /
"idempotency key" / "Redis leader lock, only for loops that genuinely must
run on one replica"): this loop uses a Redis leader lock
(`spinr:lock:insurance_period_reconciler`, `redis_set_nx`, TTL just under
the 10-min interval), the same choice `stale_p3_closer.py` and
`stale_intent_reconciler.py` make for the same reason — it's a pure
convergence job (WS-12 §3's own framing) where running on >1 replica
concurrently in the same tick would just double the read+RPC-call work, not
cause any data corruption (the RPC's `noop` handling absorbs a
double-transition attempt), but the lock avoids that redundant work anyway.
If Redis is down, `redis_set_nx` fails open (per `utils/redis_client.py`'s
documented fallback) and every replica runs its own tick — still safe
because the RPC itself de-duplicates, just more write attempts than
necessary; this is the same fail-open behavior the two sibling loops
already accept.

**Blast radius beyond `insurance_periods.py`'s caller set:** isolated. This
PR adds one new file, one new loop registration, one new alert rule, and
doc/count updates. It does not modify the ride state machine, dispatch,
`ride_offers`, `drivers`, fares, wallets, or any existing route handler. The
`rides` and `ride_offers` and `drivers` tables are read-only from this
loop's perspective (`db_supabase.get_rows`, `columns=` projections only) —
no write path to those tables was added.

**Risk of the new downgrade-gate flag being misused:** none identified — it
defaults to off, requires an explicit `app_settings` write via the admin
dashboard's existing settings mechanism (no code path defaults it on), and
even when on only ever closes a period row via the same RPC every other
caller uses.

**Watchdog registration:** `test_lifespan_watchdog_coverage.py`'s
`ast`-based source scan (drift detector, not a live-boot test) enforces
that every `_spawn()`'d loop is registered in `_WATCHDOG_LOOP_NAMES`
exactly once; this PR updates both sides together and the pinned count
assertions, so a future accidental unregistration would still fail CI
immediately, unchanged from before this PR.

## 5. User-experience effect

None. Backend-only, no rider/driver/corporate-admin/internal-admin-facing
change. Not visible mid-session to anyone. The alert rule is
operator-facing only (pages whoever is on-call via the existing
`ALERT_WEBHOOK_URL`/Slack path), and is not yet loaded into any live
Grafana instance (same "not yet provisioned" status the file's existing two
rules already carry — see the file's own header comment).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/insurance_period_reconciler.py` | New file: 10-min self-healing loop | WS-12 §3 / C55 |
| `metrics-agent/grafana/alert-rules.yaml` | Added `spinr-insurance-period-write-failed` rule + header comment note | C55 alerting gap |
| `backend/core/lifespan.py` | Registered `insurance_period_reconciler_loop` via `_spawn` + `_WATCHDOG_LOOP_NAMES` | Wire the new loop in per the background-loop skill |
| `backend/tests/test_insurance_period_reconciler.py` | New file: 12 tests | Regression coverage for the reconciler |
| `backend/tests/test_lifespan_watchdog_coverage.py` | Updated pinned spawn/watch counts (41→42 / 40→41) and docstring | Keep the drift-detector test in sync with the new loop |
| `CLAUDE.md` | Bumped "40 background asyncio loops" → 41, "26 hold... Redis leader locks" → 27, added the new loop to the named-examples list | Stay accurate after adding a loop |
| `docs/CRITICAL_BUGS_IMPLEMENTATION_PLAN.md` | Marked WS-12 §3/§4 done + added a status-table row, documented 2 spec deviations | Close out the tracked spec item |
| `ACTION_ITEMS.md` | Closed C55, added Fix/Files/Verified notes | Close out the tracked finding |

## 7. Before / after

Purely additive — no existing behavior-changing diff. `record_period_transition`'s own code, signature, and every existing caller are unchanged.

Watchdog registry, for completeness (additive entry, not a behavior change to any existing loop):

```python
# Before — _WATCHDOG_LOOP_NAMES ends with:
            "stale_p3_closer (15min)",
            "driver_daily_rollup (30min)",
        ]
    )
```

```python
# After
            "stale_p3_closer (15min)",
            "driver_daily_rollup (30min)",
            # WS-12 §3 / C55: self-heals missed insurance-period opens.
            "insurance_period_reconciler (10min)",
        ]
    )
```

## 8. Rollback plan

No migration, no schema change, no data mutation of existing rows (the
loop only ever appends new `driver_insurance_periods` rows via the
existing RPC, same as every other caller). Two independent rollback
levers, neither requiring a second deploy of unrelated code:

- **Kill the loop without a redeploy:** the loop is a plain Python `while
  True` task started at process boot from `core/lifespan.py` — there is no
  existing "disable via app_settings" switch for it (mirroring
  `stale_intent_reconciler.py`/`stale_p3_closer.py`, neither of which has
  a kill-switch either, only `stale_p3_closer`'s *autoclose* sub-behavior
  does). If it needs to stop entirely, that requires a redeploy with the
  `_spawn(...)` call removed/commented — the same rollback shape its two
  siblings would need. This is accepted as consistent with existing
  precedent, not a new gap this PR introduces.
- **Kill only the risky half without a redeploy:** the one behavior this
  PR ships that can *change* a live driver's insurance classification
  (the online-idle downgrade) is already off by default and gated by
  `app_settings.insurance_period_reconciler_downgrade_enabled` — if it was
  ever turned on and needs to come back off, that's a single `app_settings`
  write via the existing admin mechanism, no deploy at all. The
  self-heal-upward half (opening/correcting a missing P2/3 row) has no
  kill switch because it is judged pure-upside (see §4) — if that
  judgment turns out wrong in practice, disabling it requires a redeploy
  removing/flagging the `_spawn()` call, same as the full-loop case above.
- **Alert rule:** not yet loaded into any live Grafana instance (see §5),
  so "rollback" here is simply not provisioning it, or deleting the rule
  block from the YAML before a human imports it.

## 9. Verification performed

- [x] Automated tests run (unit, mocked Supabase):
  `pytest tests/test_insurance_period_reconciler.py
  tests/test_lifespan_watchdog_coverage.py tests/test_stale_p3_closer.py
  tests/test_insurance_period_rpc.py tests/test_insurance_periods.py
  tests/test_driver_status_insurance_periods.py -q --no-cov` → **64 passed**.
  Also ran the exact command named in the task:
  `pytest tests/ -k "insurance_period or reconciler or stale_p3" -v` →
  **172 passed, 3 skipped**, plus 7 pre-existing errors in
  `tests/rls/test_money_and_safety_rls.py` unrelated to this change (they
  require a live Postgres socket per CLAUDE.md's RLS-test isolation
  instructions; running them mixed into the normal suite instead of
  isolated per those instructions produces a connection error rather than
  the documented self-skip — an environment limitation, not a regression
  from this diff).
- [ ] Manual repro steps followed in staging — not performed (no staging
  environment available in this session; see "not verified" below).
- [x] Blast-radius grep performed: `grep -rln "record_period_transition("
  backend --include="*.py"` (see §4 for the full 16-file list) plus a read
  of `stale_intent_reconciler.py` and `stale_p3_closer.py` for
  replay-safety comparison.
- [x] Reviewed against relevant CLAUDE.md conventions: insurance-period
  derivation table (Period 0-3), append-only rule (RPC-only writes, no
  direct UPDATE/DELETE), background-loop replay-safety contract
  (`spinr-background-loop` skill), additive-over-destructive release gate
  (flag-gated downgrade path), observability conventions (warning+metric
  for degraded-but-recovered, no Sentry noise for the self-heal path).
- [x] Feature-flagged the one behavior that changes existing
  classification mid-flight (`insurance_period_reconciler_downgrade_enabled`,
  default off); the self-heal-upward behavior is judged pure-addition and
  intentionally not flagged, with the reasoning stated in §4/§8.
- [x] `ruff check backend/` and `ruff format --check` on every file this PR
  touches — clean (the 40 pre-existing repo-wide `ruff check .` findings
  are all in files this PR does not touch).

### What was NOT verified

- **No live Grafana access.** The new alert rule was validated for YAML
  syntax (`python3 -c "import yaml; yaml.safe_load(...)"`) and structural
  consistency with the file's two existing rules, but not provisioned or
  fired against a real Grafana Cloud instance — same caveat the file's own
  header comment already states for the pre-existing two rules. Whether the
  PromQL expression (`sum(increase(spinr_insurance_period_write_failed_total[5m]))
  > 0`) actually evaluates and pages as intended is unconfirmed.
- **No live Supabase.** The reconciler's DB interaction (`get_rows`,
  `record_period_transition`'s RPC call) is exercised only against a mocked
  `db_supabase` in the new test file — it has never run against a real
  fleet's `rides`/`ride_offers`/`drivers`/`driver_insurance_periods` data,
  so real-world drift patterns (batch-limit truncation at scale, actual
  concurrent-write races, realistic query latency) are unverified. The
  `ACTIVE_SCAN_LIMIT = 1000` / `ONLINE_DRIVER_LIMIT = 500` constants are
  reasoned defaults for Spinr's current Saskatchewan-first fleet size, not
  load-tested.
- **No production build applicable** — backend-only Python change; no
  `admin-dashboard`/`rider-app`/`driver-app` build was run because none of
  those surfaces were touched.
- **No visual regression tooling applicable** — backend-only, not a UI
  surface.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (see §8)
- [x] Blast radius is stated, not assumed (see §4)
- [x] No silent behavior change to an already-shipped flow — the one
  live-classification-changing behavior (online-idle downgrade) is
  off by default and requires an explicit operator opt-in
