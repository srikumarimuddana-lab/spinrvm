# T2 — spinr-dispatch-reviewer retro pass: dispatch claim path

**Date:** 2026-09-02
**Reviewer:** Surya (Senior Backend Developer, Spinr)
**Scope:** Phase 0 / T2 of `ACTION_ITEMS.md` C50 (PostgREST → direct pool
migration plan, `docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md`).
Retro code-review pass on the dispatch claim path, which C50's own text
flags as "self-disclosed as never run" in
`docs/change-log/2026-08-27-p2-dispatch-loop-optimization.md`.
**Review-only.** No behavior changes were made to `matching.py` or
`driver_repo.py`. This document plus new backlog entries in
`ACTION_ITEMS.md` (C54–C56) are the sole deliverables.

## Files reviewed

- `backend/routes/rides/matching.py` — `_match_driver_to_ride_attempt`,
  specifically the batch-claim loop, `ride_offers` insert, and insurance
  Period-2-open loop (lines ~821–888 at review time).
- `backend/repositories/driver_repo.py` — `claim_driver_atomic` (lines
  ~241–298) and `set_driver_available` (lines ~144–196), read for the
  release/rollback path.
- Supporting context read to verify claims rather than trust comments:
  `backend/utils/insurance_periods.py` (`record_period_transition`),
  `backend/migrations/253_insurance_period_transition_rpc.sql` (the atomic
  close+open RPC), `backend/utils/driver_claim_reaper.py` (the orphan-claim
  reaper, C3), `backend/utils/stale_p3_closer.py` (Period-3 equivalent —
  used as a design comparison, since no Period-2 equivalent exists),
  `backend/tests/test_dispatch_match_attempt_branches.py`,
  `backend/tests/test_driver_repo_coverage.py`,
  `backend/tests/test_insurance_period_rpc.py`,
  `backend/routes/drivers/ride_flow.py` (accept-path Period-2 safety net and
  loser-driver release), `backend/routes/rides/matching.py`'s offer-timeout
  path (`process_expired_offer` — the single idempotent expiry gate shared
  by the in-process timer and the durable offer-expiry reaper).

## What was checked, and what's fine

### 1. Claim atomicity and the candidate→claim staleness window — **fine, verified (not just trusted)**

`claim_driver_atomic` (driver_repo.py:271–290) issues a single conditional
`UPDATE drivers SET is_available=false, availability_claimed_at=now() WHERE
id=$1 AND is_available=true RETURNING *`. This is genuinely atomic at the
Postgres level — no read-then-write race exists inside this function. The
`.eq("is_available", True)` predicate means a losing concurrent caller gets
zero rows back (`None`), not a partial or double claim.

The docstring's claim that the code comment in `matching.py` (lines 833–838)
makes about closing the "candidate read → claim" staleness window was
independently verified by reading the code, not accepted on the comment's
word: the claim loop (matching.py:823–842) re-checks `fresh.get("is_online")
and fresh.get("is_verified") and fresh.get("status") == "active"` against
the **row returned by the UPDATE itself**, not a follow-up SELECT. Since
`RETURNING *` reflects the row state at the instant of the atomic write,
this genuinely closes the gap where an admin suspends/needs-reviews a driver
between the initial candidate SELECT (up in the dispatch attempt) and the
claim. Verdict: the comment's claim is accurate.

### 2. Error handling — DB/claim/insert failures surfaced loudly — **fine**

- The `ride_offers` insert failure path (matching.py:860–871) does
  `logger.error(..., exc_info=True)`, releases every already-claimed driver
  via `set_driver_available(d["id"], True)`, then **re-raises**. This is
  covered by a regression test
  (`test_ride_offers_insert_failure_releases_claims_and_reraises` in
  `test_dispatch_match_attempt_branches.py`) that asserts both the release
  call and the re-raise. `match_driver_to_ride`'s outer shell
  (matching.py:152–169) catches this re-raised exception, logs it, and
  re-arms `_dispatch_retry` — so the ride is never stranded in `searching`
  on a transient write failure. This matches AGENTS.md's "do not silently
  swallow errors" rule precisely: loud log with full exception, then a path
  that lets the caller/retry chain recover, not a soft fallback.
- `claim_driver_atomic` itself has no defensive try/except around its own
  `run_sync` call — a raised `DatabaseError` (per `_base.py`'s `run_sync`,
  which raises a typed `DatabaseError` on a real DB failure rather than
  returning `None`) propagates straight out of `claim_driver_atomic` into
  the claim loop. That propagation is itself correct per AGENTS.md (a DB
  blip should not masquerade as "driver unavailable" — see the existing
  regression suite in `test_dispatch_db_errors.py`, which pins exactly this
  behavior for the sibling `match_and_claim_driver` helper). What is *not*
  covered is what happens to **already-claimed** drivers from earlier loop
  iterations when this exception fires mid-loop — see Finding C54 below.

### 3. `record_period_transition`'s swallow-on-failure — **an accepted, documented exception to the no-swallow rule; fine as designed**

The module docstring in `insurance_periods.py` (lines 13–19) explicitly
frames this as a deliberate compliance trade-off: "a missed audit row is
preferable to blocking the driver state machine," logged at `ERROR` (not
`warning`) with `exc_info=True`, plus a dedicated failure metric
(`spinr_insurance_period_write_failed_total`). This is not an
undocumented silent swallow — it is:

- Backed by an alerting contract (`docs/runbooks/deploy-migration-64-65.md`
  §"Alert: `spinr_insurance_period_write_failed_total` > 0" says "Alert
  immediately if non-zero").
- Backed by a regression test asserting the swallow + metric exist
  (`test_insurance_period_rpc.py::TestPythonUsesRpc::test_still_swallows_exceptions`
  and `test_emits_failure_metric`).
- The underlying write itself is a single-transaction atomic RPC
  (migration 253, `record_insurance_period_transition`) that closes the
  prior open period and opens the new one together, closing the older
  two-step close/open crash window that originally motivated WS-12/C3.

Given CLAUDE.md/AGENTS.md's own insurance-period rules were the source of
this design (see `docs/CRITICAL_BUGS_IMPLEMENTATION_PLAN.md` §WS-12, which
this exact trade-off was reviewed and shipped from), this is judged an
**acceptable, explicit exception**, not a gap — with one caveat: the
alerting is a documented runbook contract, not something this review could
verify is *actually wired* in the live Grafana/Sentry alert config (only
`spinr_dispatch_offer_to_accept_duration_ms` and a payment-settlement-failure
rule were found in `metrics-agent/grafana/alert-rules.yaml`; no
`spinr_insurance_period_write_failed_total` rule exists there). See Finding
C55.

### 4. Insurance Period 2 timing / batch-offer model correctness — **fine, matches the AGENTS.md rule as adapted for batch-offer**

CLAUDE.md/AGENTS.md's rule is "Period 2 starts on `driver_assigned` (not
`driver_accepted`) because the driver is already obligated to the ride."
This codebase's batch-offer model has no separate `driver_assigned` DB
write — multiple drivers are claimed and offered simultaneously, and the
ride stays in `searching` until one accepts. The in-code comment
(matching.py:873–884) argues Period 2 opening at claim/offer time is the
correct analog because `claim_driver_atomic` already makes the driver
unavailable for any other ride the instant the claim succeeds — i.e., the
driver is obligated (removed from the pool) at that moment, which is the
same causal trigger the rule is protecting against (a driver who is
committed to a ride but still shown as covered only by contingent/Period-1
liability). This reasoning holds up under inspection. The accept path
(`ride_flow.py:386-398`) re-calls `record_period_transition(driver, 2,
ride_id=ride_id)` as an explicit documented no-op safety net (the RPC's
`noop` status when the same period+context is already open), not a second
real transition — consistent with append-only.

### 5. Append-only transitions — **fine, enforced at the DB layer**

Migration 253's RPC does an `UPDATE ... SET ended_at = now()` on the prior
open row (never a delete or overwrite of historical rows) plus a fresh
`INSERT`, inside one transaction, serialized by the partial unique index
`driver_insurance_periods_open` (`(driver_id) WHERE ended_at IS NULL`). No
Python code path in `insurance_periods.py` does a raw `UPDATE`/`INSERT`
against `driver_insurance_periods` outside this RPC — pinned by
`test_insurance_period_rpc.py::test_no_two_step_close_insert`, which greps
the source for exactly that anti-pattern.

### 6. Rollback completeness on the `ride_offers` insert failure path — **fine, verified**

`set_driver_available(driver_id, True)` (driver_repo.py:144–196):
- Clears `availability_claimed_at` back to `None` when `available=True`
  (lines 155–156) — so a released driver does not later look like an
  orphaned claim to the reaper.
- Calls `invalidate_driver_cache` both **before** the underlying UPDATE
  (line 149) and **after** it completes, keyed by the returned row's
  `user_id` (lines 194–195) — both sides of the cache are invalidated, not
  just one, matching the same two-sided pattern `claim_driver_atomic` itself
  uses.
- Clamps `is_available` to the driver's actual `is_online` state (reads
  `is_online` in the same query as `total_rides`, lines 167, 171–172) —
  enforces the `is_available ⇒ is_online` invariant even on the release
  path, so a driver who went offline mid-dispatch does not get incorrectly
  re-marked available.

### 7. `max_offers` boundary — **fine, no off-by-one**

The loop guard `if len(claimed_drivers) >= max_offers: break`
(matching.py:824–825) is checked **before** each `claim_driver_atomic` call,
so at most `max_offers` drivers are ever claimed in a single attempt — an
off-by-one here (checking after the claim) would have let the loop
over-claim by one driver on every attempt; it does not.

### 8. Double-claim risk — **fine, prevented at the DB layer, not just app logic**

Covered by Finding 1 above — the atomicity is a single SQL statement, not
an app-level check-then-write, so there is no window for two concurrent
dispatch attempts to both claim the same driver.

### 9. Orphan-claim reaper coverage — **fine, and covers more than its own stated scope**

`utils/driver_claim_reaper.py`'s docstring frames its job narrowly ("a
crash or restart in that window leaves the driver `is_available=false` with
no offer"), but its actual query is generic: any driver that is
`is_online=True, is_available=False`, whose `availability_claimed_at` is
older than `RECLAIM_THRESHOLD_SECONDS` (90s), with **no pending offer and no
active ride**, gets released. This condition is agnostic to *why* the
driver never got an offer — it equally covers the mid-claim-loop exception
scenario in Finding C54 below, not just the crash/restart case it was
originally written for. See C54 for why this is a partial mitigation
(recovery within ~90–150s, not immediate) rather than a full substitute for
symmetric handling in the claim loop itself.

## Gaps / risks found (filed to `ACTION_ITEMS.md`)

| # | Finding | Severity | Filed as |
|---|---|---|---|
| 1 | Claim-loop mid-iteration exception leaves earlier already-claimed drivers unreleased until the 90–150s orphan-claim reaper cycle, unlike the symmetric release-on-failure the `ride_offers` insert path already has | Moderate | `ACTION_ITEMS.md` C54 |
| 2 | No automated Period-2/general insurance-period reconciler exists (only Period-3 has one, `stale_p3_closer.py`); the write-failure alert path described in the runbook has no confirmed live Grafana/Sentry rule in `metrics-agent/grafana/alert-rules.yaml` | Minor | `ACTION_ITEMS.md` C55 |
| 3 | `claim_driver_atomic`'s `run_sync` call for what is functionally a write uses the default `"read"` retry policy (3 attempts w/ backoff) rather than an explicit `"write"`/`"idempotent_write"` policy; currently safe by construction (a retried-after-success call just returns `None`, which the caller correctly treats as "not claimed" and does not double-claim) but undocumented and inconsistent with this file's own read/write retry-policy convention | Informational | `ACTION_ITEMS.md` C56 |

No `critical` findings were filed. Every gap identified above already has
a partial or full runtime mitigation that this review could point to in the
existing code (the orphan-claim reaper for #1; the ERROR-level log + metric
+ existing accept-path/offer-timeout self-healing for #2; the WHERE-guarded
UPDATE's safe-false-negative property for #3) — consistent with this
review's instruction not to invent critical severity for already-mitigated
issues.

## Trivial fixes applied directly

None. Every finding above requires either new logic (a try/except with
release semantics around the claim loop; a new reconciler loop; an explicit
retry-policy decision that changes retry counts and could shift race
behavior under load) — none qualified as a one-line, zero-risk change safe
to make without touching `matching.py`'s or `driver_repo.py`'s actual
claim/offer/insurance logic. All three are filed as backlog entries per the
task's "when in doubt, file a backlog entry instead of fixing" instruction.
