# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude Code (WS-1 executing session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, dispatch |
| PR / commit link | (attached to the WS-1 PR) |
| Related issue or gap ID | ACTION_ITEMS.md C54; `docs/audit/2026-09-03-engineering-director-teardown-round2.md`; `plans/2026-09-03-path-to-a-implementation-plan.md` WS-1 subtasks 1-4 |

This log covers WS-1 subtasks 1-4 (four independent fixes in two files).
Subtask A (admin force-cancel state-machine guard,
`backend/routes/admin/rides.py`) has its own log:
`docs/change-log/2026-09-03-admin-cancel-state-guard.md`.

## 1. Issue / gap identified

Four correctness gaps on live-tested paths, all sharing the same root
cause class (an error or a race silently produces a wrong-but-plausible
outcome instead of surfacing loudly):

1. `settle_corporate`'s `corporate_billing_enabled` kill-switch read
   failure logged a warning and **proceeded as if the flag were enabled**
   — the kill switch could not be trusted during exactly the DB-degradation
   incident it exists to guard against.
2. `_atomic_settle_enabled`'s `ledger_atomic_settle_enabled` read failure
   was silently swallowed at `warning` with no counter — the fallback
   itself is safe, but the failure was invisible.
3. `_match_driver_to_ride_attempt`'s PostgREST claim loop had no
   `try/except`: a `claim_driver_atomic` exception on candidate N left
   every driver claimed at candidates 1..N-1 stranded `is_available=false`
   until the orphan-claim reaper's ~150s worst case.
4. `_offer_timeout_handler` reverted a ride from `driver_assigned` back to
   `searching` with an `{"id": ride_id}`-only filter — a TOCTOU race where
   the driver accepts in the same window the handler wakes up would be
   silently overwritten, and (found while fixing this) the miss-streak
   increment / driver release / auto-offline logic ran unconditionally
   ahead of that same race window too.

## 2. Root cause

1-2. Both flag reads use the same `try: ... except Exception: logger.warning(...)`
copy-paste shape with no counter — a settings-read error was never
distinguished from "flag genuinely off," so the caller (and any dashboard)
had no way to tell the two apart.

3. The claim loop was added without a `try/except`, unlike the very similar
`ride_offers` insert step immediately below it in the same function, which
already has the release-on-failure pattern this fix mirrors.

4. The handler's only race guard was a single, non-atomic read-then-branch
at the top of the function; the actual state-changing write (and, more
seriously, the driver-release/miss-streak side effects ahead of it) never
re-checked that the race hadn't already been lost.

## 3. Fix / remediation

1. `settle_corporate` now returns `PaymentResult(success=False,
   status_code=503)` on a settings-read exception — identical to the flag
   being explicitly `False`. Fails closed.
2. `_atomic_settle_enabled` keeps its existing behavior (fall back to the
   legacy settle path) but now logs at `error` with `exc_info=True`. Both
   1 and 2 increment `spinr_payment_settings_read_failed_total{flag=...}`.
   The asymmetry (1 fails closed, 2 keeps its fallback) is deliberate — see
   `docs/adr/011-flag-read-failure-semantics.md`.
3. The claim loop is wrapped in `try/except Exception`: on failure, every
   driver in `claimed_drivers` is released via `set_driver_available` before
   the exception is logged (`error`, `exc_info=True`) and re-raised.
4. The revert-to-searching write moved earlier in the function and became a
   conditional `update_one({"id", "status": driver_assigned, "driver_id"},
   ...)` — the same optimistic-lock pattern `routes/drivers/
   ride_flow.py:331` uses for driver-side accept. 0 rows (race lost) now
   short-circuits the **entire** rest of the function: no miss-streak
   increment, no driver release/auto-offline, no notifications, no
   re-dispatch.

## 4. Risk & impact on existing functionality

- **Subtask 1** — blast radius: `settle_corporate` has one production
  caller, `backend/routes/rides/payments.py`. It already handles a 503
  `PaymentResult` (the pre-existing "flag explicitly off" branch returns
  the identical shape); this change only widens *when* that branch is
  taken, not its shape. No other reader/writer of `corporate_billing_enabled`
  exists besides the admin settings write path and
  `tests/test_kill_switch_flags.py` (schema-shape tests only, unaffected).
- **Subtask 2** — blast radius: `_atomic_settle_enabled` has one call site,
  inside `payment_service.py` itself, gating RPC vs. legacy settle. No
  behavior change on the happy path or the failure path's *outcome* (still
  legacy), only its logging/counting.
- **Subtask 3** — blast radius: isolated to the PostgREST claim loop inside
  `_match_driver_to_ride_attempt`. The direct-pool branch (flag-gated,
  currently off) is untouched — it has its own, already-correct exception
  handling (single RPC transaction, nothing to release on failure). No
  change to the successful-claim path or the existing `ride_offers`
  insert-failure path.
- **Subtask 4** — blast radius: `_offer_timeout_handler` is spawned
  fire-and-forget from the dispatch offer loop; no caller reads its return
  value. Reordering the conditional update ahead of the miss-streak/release
  logic changes *when* driver-state side effects happen relative to each
  other, but they have no data dependency (the ride update doesn't read
  driver state; the driver release doesn't read ride state), so this is
  safe. This closes a insurance-Period-2 exposure beyond what the plan's
  literal wording named (see the code comment at the fix site and
  `ACTION_ITEMS.md` C54) — flagged explicitly here since it widens the fix
  slightly past the plan's narrowest reading, in service of the same race.

## 5. User-experience effect

- Subtask 1: a rider/company on a corporate-paid ride during a genuine
  settings-read outage now sees a payment retry instead of a
  silently-accepted charge that bypassed an active kill switch — this is a
  behavior change only during an already-degraded state, not in normal
  operation.
- Subtask 2: no user-visible change (logging/counting only).
- Subtask 3: no user-visible change in the common case; during a transient
  PostgREST error, drivers claimed earlier in the same dispatch attempt are
  now available again within the request instead of ~150s later — strictly
  better for driver-side availability and rider match rate.
- Subtask 4: a driver who accepts in the exact ~0-width race window no
  longer has their acceptance silently reverted, and is no longer at risk
  of being wrongly released to the available pool or auto-offlined while
  correctly obligated to the ride (insurance Period 2). Rider-visible only
  in that this race window, however narrow, no longer produces a
  "Finding another driver" message for a ride that was actually accepted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/payment_service.py` | `settle_corporate`: fail-closed on settings-read error + counter. `_atomic_settle_enabled`: error-level log + counter, same fallback. | WS-1 subtasks 1-2 |
| `backend/routes/rides/matching.py` | PostgREST claim loop wrapped in try/except with release-on-failure. `_offer_timeout_handler`: conditional revert-to-searching moved earlier, gates the rest of the function. | WS-1 subtasks 3-4 |
| `backend/tests/test_corporate_kill_switch_fail_closed.py` (new) | Tests for both flag read-failure behaviors + counter. | WS-1 subtasks 1-2 |
| `backend/tests/test_booking_new_ride_requests_kill_switch.py` | Docstring correction: no longer cites `settle_corporate` as a fail-open precedent. | Kept accurate after subtask 1 |
| `backend/tests/test_dispatch_db_errors.py` | New regression test for the claim-loop release. | WS-1 subtask 3 |
| `backend/tests/test_offer_timeout.py` | Fixed `test_expires_and_resets`'s mock (was implicitly returning `None`, now returns a truthy row); new `test_race_lost_skips_release_notify_and_redispatch`. | WS-1 subtask 4 |
| `docs/adr/011-flag-read-failure-semantics.md` (new) | Records the fail-open vs. fail-closed decision per flag. | WS-1 subtask 2 |
| `ACTION_ITEMS.md` | C54 closed. | WS-1 subtask 3 |

## 7. Before / after

**Subtask 1 — `settle_corporate` kill-switch:**
```python
# Before
    except Exception as settings_err:
        logger.warning("[PAYMENT] app_settings lookup failed ({}), proceeding as enabled", settings_err)

# After
    except Exception as settings_err:
        logger.error(
            "[PAYMENT] app_settings lookup failed ({}), failing closed on corporate_billing_enabled",
            settings_err,
            exc_info=True,
        )
        _metric_inc("spinr_payment_settings_read_failed_total", {"flag": "corporate_billing_enabled"})
        return PaymentResult(success=False, error="Corporate billing is temporarily unavailable", status_code=503)
```

**Subtask 3 — dispatch claim loop:**
```python
# Before
                for driver, eta_sec, _ in ranked:
                    if len(claimed_drivers) >= max_offers:
                        break
                    fresh = await _deps.db_supabase.claim_driver_atomic(driver["id"])
                    ...

# After
                try:
                    for driver, eta_sec, _ in ranked:
                        if len(claimed_drivers) >= max_offers:
                            break
                        fresh = await _deps.db_supabase.claim_driver_atomic(driver["id"])
                        ...
                except Exception as e:
                    logger.error(f"[DISPATCH] postgrest claim loop failed for ride {ride_id}: {e}", exc_info=True)
                    for d, _ in claimed_drivers:
                        await _deps.db_supabase.set_driver_available(d["id"], True)
                    raise
```

**Subtask 4 — offer-timeout revert:**
```python
# Before (unconditional, after miss-streak/release logic already ran)
        await _deps.db.update_one(
            "rides", {"id": ride_id},
            {"$set": {"status": RideStatus.SEARCHING, "driver_id": None, ...}},
        )

# After (conditional, gates everything below it)
        reverted = await _deps.db.update_one(
            "rides", {"id": ride_id, "status": RideStatus.DRIVER_ASSIGNED, "driver_id": driver_id},
            {"$set": {"status": RideStatus.SEARCHING, "driver_id": None, ...}},
        )
        if reverted is None:
            logger.info(f"[DISPATCH] Offer timeout for ride {ride_id} driver {driver_id} lost the race — ...")
            return
        # miss-streak / auto-offline / release / notify / re-dispatch follow, now guarded
```

## 8. Rollback plan

- All four fixes are `git revert`-safe: none write data differently on
  their respective happy paths, and none touch a migration or a stored
  flag default.
- No feature flag needed — each change is either strictly safer (1, 3, 4)
  or logging/counting-only (2).

## 9. Verification performed

- [x] Automated tests written: `test_corporate_kill_switch_fail_closed.py`
  (4 tests), `test_dispatch_db_errors.py`
  (`test_postgrest_claim_loop_releases_prior_claims_and_reraises`),
  `test_offer_timeout.py` (fixed 1 existing test, added 1 new test).
- [ ] **Not run in this session** — this sandboxed environment's network
  egress policy blocks PyPI (`pypi.org` / `files.pythonhosted.org` both
  return `403 Host not in allowlist`), so `backend/requirements.txt` could
  not be installed and `pytest` could not execute. See "What was NOT
  verified" below.
- [x] Static verification performed instead: `python3 -m py_compile` and
  `ruff check` / `ruff format --check` on every touched file (all clean),
  plus manual line-by-line tracing of every changed call site against its
  actual callers/return types/mock defaults (e.g. confirmed
  `unittest.mock.AsyncMock()`'s default awaited return is a truthy
  `MagicMock`, not `None`, before relying on that to keep 5 pre-existing
  `_offer_timeout_handler` tests passing unmodified).
- [x] Blast-radius grep performed: `settle_corporate(` and
  `_atomic_settle_enabled(` callers (1 each), `_offer_timeout_handler`
  callers/mocks (5 test files, 2 needed updates), `claim_driver_atomic`
  claim-loop callers (isolated), and a repo-wide grep for the
  `"proceeding as enabled"` warning pattern (6 other unrelated kill
  switches found and deliberately left unchanged — see the ADR).
- [x] Reviewed against `CLAUDE.md`: ride state machine (subtask 4's guard
  mirrors the documented `ride_flow.py:331` optimistic-lock pattern),
  insurance-period rules (subtask 4), Decimal/money rules (n/a — no
  arithmetic changed), "do not silently swallow errors" (all four
  subtasks), observability conventions (error-level logging + Prometheus
  counter naming `spinr_<domain>_<metric>_<unit>`).
- [ ] Manual repro steps followed in staging — no staging environment
  exists yet in this session (WS-4 provisions it; tracked separately,
  H1/H2 in the plan).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, stated above).
- [x] Blast radius is stated, not assumed (§4, plus the six-kill-switches grep in the ADR).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5).

## What was NOT verified

- **No automated test execution in this session.** This sandbox's network
  egress policy blocks `pypi.org`/`files.pythonhosted.org` (confirmed via
  direct `curl`, which returned `403 Host not in allowlist` from both —
  not a proxy misconfiguration, a deliberate policy denial per
  `/root/.ccr/README.md`), so `pip install -r requirements.txt` could not
  complete and `pytest` was never actually run against these changes. Every
  test in this change-log was written and traced by hand against the real
  production code paths (return types, call signatures, existing mock
  behavior) rather than executed. **This must be run in CI (or a
  properly-provisioned dev environment) before merge** — do not treat the
  static checks above as a substitute for an actual green test run.
- Not tested against a live Supabase or real `app_settings` row — only
  reasoned about via `settings_loader.get_app_settings()`'s documented
  behavior (defaults on an empty/missing row).
- No load/concurrency test exercised the subtask 4 race window for real
  (two concurrent requests actually racing); the fix's correctness rests on
  the conditional `update_one`'s atomicity at the PostgREST/Postgres layer,
  the same primitive the pre-existing driver-accept guard already relies
  on in production.
- No visual/UI verification — this PR is backend-only.
