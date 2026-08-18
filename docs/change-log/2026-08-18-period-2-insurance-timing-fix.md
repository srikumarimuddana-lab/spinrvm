# Change Impact & Risk Log — Period 2 insurance-coverage timing fix

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code session (see PR for attribution) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch / safety (insurance-period classification) |
| PR / commit link | (this commit, branch `claude/spinr-app-all-surfaces-de596c`) |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` ranked blocker #1/#2; `ACTION_ITEMS.md` A40 |

## 1. Issue / gap identified

Insurance Period 2 (TNC primary commercial coverage, "en route to pickup") was opening at
`driver_accepted` instead of at the moment a driver is claimed/offered a ride — contradicting both
CLAUDE.md's own stated rule ("Period 2 starts on `driver_assigned`... because the driver is already
obligated to the ride") and the already-published `docs/legal/terms-of-service.md` §13, which tells
drivers coverage starts "from the moment you are assigned through drop-off."

## 2. Root cause

The production dispatch model is batch-offer, not the single-assignment model CLAUDE.md's table
describes: `match_driver_to_ride` (`backend/routes/rides/matching.py`) claims up to `max_offers`
drivers atomically and inserts `ride_offers` rows, but the `rides.status` column never transitions
through `driver_assigned` — it stays `searching` for the whole offer window. `assign_driver_to_ride`
(`backend/services/dispatch_service.py`), the only function that would have written that status, has
zero callers outside tests (confirmed dead code). Because nothing in the live code path ever reached
a `driver_assigned` state, `record_period_transition(driver_id, 2, ...)` was only ever called at
`accept_ride` (`backend/routes/drivers/ride_flow.py:372`) — after the driver had already been
unavailable-for-other-rides (claimed, offer pending) for up to 15 seconds × cascade attempts.

## 3. Fix / remediation

`match_driver_to_ride` now calls `record_period_transition(driver_id, 2, ride_id=ride_id)` for every
successfully claimed driver, immediately after their `ride_offers` row is persisted — the real
obligation moment, matching the claim atomicity (`claim_driver_atomic`) that already makes the driver
unavailable for any other ride. If the `ride_offers` insert itself fails, the claims are released
before Period 2 would ever have opened (no orphaned Period 2 row for an offer that never existed).

The existing accept-time call in `ride_flow.py` is left in place as a defense-in-depth no-op safety
net (the RPC returns `status: "noop"` when the same period+driver is already open) rather than
removed, so accept-time still guarantees Period 2 is open with the correct `ride_id` even if
something unexpected closed it between claim and accept.

`decline_ride` (`ride_flow.py`) previously called `record_period_transition(driver_id, 1)`
unconditionally on decline. Now that Period 2 genuinely opens for every offer, that unconditional
call needed the same guard `process_expired_offer` (`matching.py`) already uses: only record Period 1
if `set_driver_available`'s result actually shows `is_available: True`. Without this guard, a driver
who toggled offline between the offer landing and declining it would get a false Period 1 row
reopened over their correct Period 0 (they're already offline).

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), isolated to two files that already owned this logic.**

- **Writers of `driver_insurance_periods` touched:** `backend/routes/rides/matching.py`
  (`match_driver_to_ride`) and `backend/routes/drivers/ride_flow.py` (`decline_ride`; `accept_ride`'s
  existing call is unchanged in behavior, only its comment). No other writer of this table exists —
  grepped every `record_period_transition` call site (16 across `routes/`, `services/`, `utils/`);
  all others are unrelated transitions (go-online/offline, ride-complete, admin actions) and are not
  touched by this change.
- **Readers of `driver_insurance_periods` — grepped, none require a change:**
  `services/data_transfer/*` (DSAR export/import, reads the timeline as-is), `services/test_account_cleanup_service.py`,
  `scripts/verify_restore.py`, `core/lifespan.py` (background-loop registration only, doesn't read
  rows), `utils/driver_activity.py` (derives online-time metrics from the timeline),
  `utils/retention_guard_monitor.py` (checks the append-only guard triggers are intact, doesn't
  interpret period values), `utils/stale_in_progress_ride_alerter.py` (closes Period 3 rows, unrelated
  to Period 1/2), `routes/users.py` (deletion-eligibility guard — blocks hard-delete if any period rows
  exist, doesn't care which), `routes/admin/drivers.py` (admin-facing period history display). All of
  these consume the timeline generically; none assume Period 2 opens only at acceptance.
- **Observable behavior change:** `utils/driver_activity.py`'s derived "time online, no ride" (Period
  1) metric will be slightly shorter per driver going forward — offer-pending windows (up to 15s ×
  cascade attempts) now correctly carve out as Period 2 instead of counting as Period 1. This is the
  intended, more-accurate behavior, not a regression, but any dashboard trending Period-1 online-time
  week-over-week will show a small step change on this deploy date. Not flagged as user-visible (no
  rider/driver/admin screen currently surfaces raw period-1-duration).
- **Money/wallet/ride-state machine:** not touched. This change writes only to
  `driver_insurance_periods` (append-only, `record_period_transition`'s existing atomicity/locking is
  unchanged) and does not read or write `rides.status`, any wallet table, or any Stripe-adjacent code.
- **Background loops:** none of the 18 background loops in `core/lifespan.py` call the touched
  functions; no loop-replay-safety concern.
- **Does NOT touch:** `assign_driver_to_ride` / `_offer_timeout_handler` (confirmed dead code, left
  as-is — a separate cleanup item, not part of this fix's scope).

## 5. User-experience effect

None. This is a backend-only audit-trail timing correction — no rider, driver, corporate-admin, or
internal-admin screen changes behavior, copy, or timing of any user-visible event. The only consumer
of the corrected timing is the regulatory/insurance audit trail itself (`driver_insurance_periods`),
which is never rendered directly to a rider or driver; the admin period-history view
(`routes/admin/drivers.py`) will show more (and more accurate) Period 2 rows going forward, which is
the intended fix, not a new UX surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | `match_driver_to_ride` now opens Period 2 for every claimed driver immediately after the `ride_offers` insert succeeds | Move the "obligated to the ride" moment to match reality — claim, not accept |
| `backend/routes/drivers/ride_flow.py` | `decline_ride`'s Period-1 close now guards on `set_driver_available`'s actual `is_available` result (was unconditional); `accept_ride`'s existing Period-2 call comment updated to describe it as a safety-net no-op | Prevent a false Period-1 reopen for a driver who went offline before declining; keep the comment accurate |
| `backend/tests/test_rides_matching_coverage.py` | Added 2 tests: Period 2 opens for every claimed driver post-insert; Period 2 never opens if the `ride_offers` insert fails | Regression coverage for the new transition |
| `backend/tests/test_driver_ride_flow_coverage.py` | Added 2 tests: Period 1 recorded on decline only when the driver is still actually available; skipped when they went offline first | Regression coverage for the guard fix |
| `docs/legal/insurance-coverage-periods.md` | Pre-publication note #2 marked RESOLVED — the page's own wording was already correct, only the code needed to catch up | Close the loop the audit flagged |
| `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` | Ranked blocker #1/#2 and their decision-log/NEW-findings rows marked FIXED with evidence | Keep the audit's own ledger accurate, per its Prior-Findings-Ledger convention |
| `ACTION_ITEMS.md` | A40 (whole-app audit tracking entry) annotated with the fix; disambiguation note added for the pre-existing, unrelated A40 discovered mid-edit | Same ledger-accuracy requirement; numbering-collision transparency |

## 7. Before / after

```python
# Before — backend/routes/rides/matching.py, after the ride_offers insert:
    except Exception as e:
        logger.error(f"[DISPATCH] ride_offers insert failed: {e}", exc_info=True)
        for d, _ in claimed_drivers:
            await _deps.db_supabase.set_driver_available(d["id"], True)
        raise

    _metric_inc("spinr_dispatch_offer_sent_total", by=len(offer_rows))
    # (Period 2 was not opened until accept_ride, in a different file, later)
```

```python
# After
    except Exception as e:
        logger.error(f"[DISPATCH] ride_offers insert failed: {e}", exc_info=True)
        for d, _ in claimed_drivers:
            await _deps.db_supabase.set_driver_available(d["id"], True)
        raise

    # Insurance Period 2 opens HERE, at claim/offer time, not at acceptance.
    for d, _ in claimed_drivers:
        await _deps.record_period_transition(d["id"], 2, ride_id=ride_id)

    _metric_inc("spinr_dispatch_offer_sent_total", by=len(offer_rows))
```

```python
# Before — backend/routes/drivers/ride_flow.py, decline_ride:
    await db_supabase.set_driver_available(driver["id"], True)
    await _deps.record_period_transition(driver["id"], 1)
    await reset_miss_streak(driver["id"])
```

```python
# After
    released = await db_supabase.set_driver_available(driver["id"], True)
    if isinstance(released, dict) and released.get("is_available"):
        await _deps.record_period_transition(driver["id"], 1)
    await reset_miss_streak(driver["id"])
```

## 8. Rollback plan

**Code revert is a sufficient rollback plan here** (an exception to the general "git revert isn't a
rollback plan" rule, which applies to changes already reflected in external state like Stripe charges
or wallet deltas): this change writes new rows to an append-only audit table
(`driver_insurance_periods`) going forward. Reverting the commit stops new Period-2-at-claim rows from
being written on the next deploy; no in-place data mutation occurred, and no already-written row needs
correcting — the fix is purely about *when* future rows open, not a backfill or correction of past
rows. No feature flag was used (the fix corrects an audit-trail write to already-truthful driver
state — `is_available` was already false during the offer window; gating that off would reintroduce
the miss-classification, not protect against a bad rollout) and no migration was touched.

## 9. Verification performed

- [x] Automated tests added and run: `backend/tests/test_rides_matching_coverage.py` (2 new tests:
  `test_period_2_opened_for_each_claimed_driver_after_offer_insert`,
  `test_period_2_not_opened_when_offer_insert_fails`) and
  `backend/tests/test_driver_ride_flow_coverage.py` (2 new tests:
  `test_period_1_recorded_when_release_leaves_driver_available`,
  `test_period_1_skipped_when_driver_went_offline_before_decline`) — unit tests, mocked
  `db_supabase`/`record_period_transition` per this repo's convention (`mock_supabase_client`-style
  patching of `_deps`).
- [x] Full affected-module test suite run: `pytest backend/tests/test_rides_matching_coverage.py
  backend/tests/test_driver_ride_flow_coverage.py backend/tests/test_insurance_periods.py
  backend/tests/test_insurance_period_rpc.py` — see PR/commit for pass count.
- [ ] Manual repro steps followed in staging — **not performed**; no staging environment access in
  this session (static/unit-test verification only, consistent with this repo's existing test-tier
  boundary).
- [x] Blast-radius grep performed: all 16 `record_period_transition` call sites across the backend,
  and all readers of `driver_insurance_periods` (listed in §4) — confirmed only the two touched files
  write Period 2, and no reader assumes the old (accept-time-only) timing.
- [x] Reviewed against relevant CLAUDE.md conventions: insurance-period rules ("Period 2 starts on
  `driver_assigned`... because the driver is already obligated"), append-only invariant (unchanged —
  only the RPC's existing atomic close+open is called, no new write path), "do not silently swallow
  errors" (both touched call sites remain wrapped by `record_period_transition`'s own documented
  compliance-grade log-at-ERROR-and-swallow contract, unchanged).
- [ ] Feature-flagged — **not applicable**, see rollback-plan justification above (this corrects an
  audit-trail write to already-true driver state; flagging it off would restore the misclassification,
  not add safety).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (revert the commit; no data migration to unwind)
- [x] Blast radius is stated, not assumed (full call-site + reader grep in §4)
- [x] No silent behavior change to an already-shipped rider/driver/admin-facing flow — this is
  backend-only audit-trail timing; §5 states the (lack of) UX effect explicitly

## What was NOT verified

- Not exercised against a live/staging Supabase instance — no live Postgres call, no real
  `record_insurance_period_transition` RPC invocation, only unit tests with mocked `db_supabase`.
- Did not verify the fix against real multi-driver concurrent-offer traffic (e.g. two drivers claimed
  for the same ride in the same dispatch attempt, one accepts, one times out) beyond what the unit
  tests cover — the RPC's own atomicity (partial unique index, tested in `test_insurance_period_rpc.py`)
  is trusted rather than re-verified end-to-end here.
- Did not check whether any external reporting/analytics pipeline outside this repo (e.g. a BI tool
  reading `driver_insurance_periods` directly) has a hardcoded assumption about Period-2 timing — only
  in-repo readers were grepped.
- `assign_driver_to_ride` / `_offer_timeout_handler` dead-code cleanup was explicitly out of scope and
  not touched — tracked separately as a low-priority follow-up (see the audit's ranked blocker #4 for
  context on why it's flagged as a hazard).
