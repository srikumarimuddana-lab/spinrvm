# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (spinr platform, Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (see PR opened alongside this file) |
| Related issue or gap ID | ACTION_ITEMS.md C54 |

## 1. Issue / gap identified

`_match_driver_to_ride_attempt`'s PostgREST batch-claim loop (`backend/routes/rides/matching.py`) had no `try/except` around its `claim_driver_atomic` call. If that call raised on a later candidate (e.g. a transient `DatabaseError`), every driver already claimed at earlier candidates in the same loop stayed `is_available=false` with no immediate release — orphaned until the claim reaper's ~90s age threshold plus ~60s tick interval (worst case ~150s) freed them.

## 2. Root cause

Asymmetry with the `ride_offers` insert failure a few lines below, which *does* release every claimed driver via `set_driver_available` before re-raising. The claim loop was simply never given the same treatment when it was written.

## 3. Fix / remediation

Wrapped the `claim_driver_atomic` call in `try/except Exception`: on failure, log at `error` with the full traceback, release every driver already appended to `claimed_drivers` via `set_driver_available(d["id"], True)`, then re-raise — mirroring the `ride_offers` insert failure handler's exact pattern in the same function.

This fix applies to the PostgREST claim path only (`_direct_pool_enabled == False`). The direct-pool path (C50 Phase 2's `dispatch_claim_batch` RPC, already live in this file) claims, inserts `ride_offers`, and writes the insurance transition in one Postgres transaction — a mid-call failure there rolls back the whole transaction server-side, so there is nothing to release on that branch (confirmed by reading that branch's own comment before concluding no change was needed there).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** The only behavior change is what happens on a `claim_driver_atomic` exception mid-loop — the success path (every claim succeeds or returns falsy) and the already-covered `ride_offers`-insert-failure path are untouched, confirmed by the full existing `test_dispatch_match_attempt_branches.py` suite passing unmodified.
- **What else reads/writes the same state?** `set_driver_available` is also called by: the `ride_offers` insert failure handler (same function, unaffected — different exception site), `driver_claim_reaper.py` (the existing mitigation this fix makes faster, not redundant with — the reaper still exists as a backstop for crashes/restarts that skip this in-process exception path entirely), and the offer-decline/expiry paths elsewhere in the ride lifecycle (untouched, different call sites).
- **Could this regress a flow that currently works?** No — before this fix, a mid-loop exception already propagated and re-armed the retry via the outer recovery shell (`match_driver_to_ride`, lines ~152-169); this fix only adds the release step before that same re-raise, it doesn't change whether/how the ride recovers.
- **Interaction with background loops:** `driver_claim_reaper.py` is unaffected — it still runs on its own cycle as a backstop; this fix just means the common in-process-exception case no longer needs to wait for it.

## 5. User-experience effect

None directly visible. Indirect improvement: a driver whose claim attempt hit a transient DB error during another rider's dispatch attempt becomes available for new offers immediately instead of up to ~150s later — fewer false negatives in driver-app "why am I not getting offers" during a Supabase blip, and a small improvement to the match-rate KPI during that window. No UI change, no new user-facing state.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | Wrapped `claim_driver_atomic` call in the PostgREST claim loop with `try/except` that releases already-claimed drivers before re-raising | Close the release-on-exception gap C54 identified |
| `backend/tests/test_dispatch_match_attempt_branches.py` | New test `test_claim_loop_exception_releases_earlier_claims_and_reraises` | Regression coverage per C54's acceptance criterion |
| `ACTION_ITEMS.md` | Closed C54 | Fix landed |

## 7. Before/after

```diff
 for driver, eta_sec, _ in ranked:
     if len(claimed_drivers) >= max_offers:
         break
-    fresh = await _deps.db_supabase.claim_driver_atomic(driver["id"])
+    try:
+        fresh = await _deps.db_supabase.claim_driver_atomic(driver["id"])
+    except Exception as e:
+        logger.error(f"[DISPATCH] claim_driver_atomic failed for driver {driver['id']}: {e}", exc_info=True)
+        for d, _ in claimed_drivers:
+            await _deps.db_supabase.set_driver_available(d["id"], True)
+        raise
     if fresh:
         ...
```

## 8. Rollback plan

`git revert` — additive `try/except` only, no schema/data change, no second deploy step. Reverting restores the pre-fix behavior (rely on the claim reaper), which is still functionally safe, just slower to recover — not a regression to a broken state.

## 9. Verification performed

- New test `test_claim_loop_exception_releases_earlier_claims_and_reraises` (2 candidates; candidate 2 raises; asserts `set_driver_available` is called exactly once, for candidate 1 only — not candidate 2, which never reached a claimed state).
- Full `test_dispatch_match_attempt_branches.py`: 14/14 passed, including the sibling `test_ride_offers_insert_failure_releases_claims_and_reraises` unmodified.
- Broader dispatch/matching sweep (`pytest tests/ -k "dispatch or matching"`): 489 passed, 4 skipped, 0 failed.
- `ruff check` / `ruff format --check` clean on both touched files.

## 10. What was NOT verified

- No live-Postgres/live-Supabase integration test exercising a real transient `DatabaseError` from `claim_driver_atomic` under concurrent load — this is a unit-level mock-based verification, per this repo's existing test convention for this file (`mock_supabase_client` fixture; no live DB in unit tests).
- No load-test/timing measurement of the actual latency improvement (how much faster drivers become available vs. the reaper's ~150s worst case) — the fix's correctness (release happens, exception still propagates) is verified; its real-world timing benefit is reasoned from the code, not measured live.
