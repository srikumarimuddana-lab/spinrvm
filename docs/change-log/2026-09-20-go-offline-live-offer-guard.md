# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code session (agentic fleet work), branch `fix/go-offline-live-offer-guard` |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | _to be filled on PR creation_ |
| Related issue or gap ID | Alternative explicitly rejected in `docs/change-log/2026-09-20-insurance-period-derivation.md` ("Alternative considered and rejected"), revisited now that PR #5571 (that fix) has merged |

## 1. Issue / gap identified

`routes/drivers/status.py`'s Go Offline 409 guard checks for an active `rides`
row and rejects the toggle if one exists. Its own comment states the intent
plainly: *"To go offline during an offer, decline it first."* But batch
dispatch holds its claim in `ride_offers` with no `rides.driver_id` link
pre-acceptance (migration 100) — so a driver holding a live batch-dispatch
offer was never actually caught by this guard. The stated rule was never
enforced for the one case it names.

Found by code reading during the #5571 insurance-period investigation, not by
a bug report or observed incident.

## 2. Root cause

The guard was written against the wrong table for batch dispatch. It queries
`rides` — correct for the single-offer dispatch path, which does write
`rides.driver_id` at `driver_assigned` — but batch dispatch's claim lives
entirely in `ride_offers` until the driver accepts, so the same query returns
nothing for a batch-offer-holding driver.

## 3. Fix / remediation

Adds a second check inside the existing `if not is_online:` guard block: when
`go_offline_live_offer_guard_enabled` is on, also query `ride_offers` for a
fresh pending offer and 409 if one exists, with a rider-facing-safe message
("You have a pending ride offer. Please accept or decline it before going
offline."). Gated behind a **new, separate** flag from
`insurance_period_live_offer_enabled` (#5571) — deliberately not folded into
that flag, because this one changes what a driver can *do* (a previously-
allowed toggle now 409s), where #5571 only changed what gets written to an
audit table. CLAUDE.md gate #3 treats a new validation rule that could reject
previously-valid input as its own flagged decision, not a rider on someone
else's flag.

### Alternative considered and rejected

Leaving this permanently unimplemented, since the reconciler's Period 2/3
self-heal already repairs the audit-record consequence of the gap (#5571
closed that). Rejected: the reconciler fixes the *record*, not the *product*
gap. A driver can still walk away from a live offer by going offline, which
the guard's own comment says should not be possible, independent of what gets
written to `driver_insurance_periods`. This closes the actual gap the earlier
fix's Change Impact Log named and deferred.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), contained by a default-OFF flag.**

This is the one call site that changes: the Go Offline branch of
`update_driver_status` in `routes/drivers/status.py`. No other caller of that
guard exists — it is inline to this one endpoint.

**What shares this state:**

- `ride_offers` table — read-only here; no write. Same table `#5571`'s
  online-path lookup already reads.
- `_fresh_pending_offers()` — reused, not duplicated; the same staleness rule
  (`STALE_PENDING_OFFER_SECONDS = 90`) applies to both directions.
- No interaction with `insurance_period_live_offer_enabled` (#5571): that flag
  governs what gets *recorded*; this one governs whether the toggle is
  *allowed* at all. A driver blocked by this guard never reaches the period-
  recording code below it, so the two flags cannot conflict — at most, turning
  this one on makes #5571's fix less necessary in practice (fewer live-offer
  Go Offline attempts reach the point where the wrong period would have been
  recorded), never more.

**Could this regress a working flow?**

- **Yes, directly, when the flag is ON: a Go Offline tap that previously
  succeeded can now 409.** This is the entire point of the change and is the
  reason it is flag-gated rather than shipped hot. See §5.
- One extra `ride_offers` read on the Go Offline path, **only when the flag is
  ON and the request is a genuine online -> offline flip**. Indexed,
  `limit=5`, same query shape as the existing online-path lookup. Zero extra
  reads when OFF, and zero on an idempotent re-assert regardless of flag
  state — both verified by dedicated tests.
- **Settings-load failure mode (found in review, previously undisclosed).**
  `_get_offline_guard_settings()` (an alias for `settings_loader.get_app_settings`)
  is called unconditionally on every genuine offline flip, before the flag
  check — there is no way to know the flag's value without fetching it.
  `get_app_settings()` does not catch its own DB read failure (see
  `settings_loader.py`): a cold cache plus a Supabase hiccup at that moment
  raises, and `update_driver_status` has no try/except around this call, so
  Go Offline would fail even when the flag is OFF. This is not a new category
  of risk this diff invented — the identical unconditional-fetch shape already
  exists twice more in this same file (the eligibility-recheck fetch on the
  online path, and the `insurance_period_live_offer_enabled` fetch further
  down this same function) — but it is now exercised on the offline path for
  the first time, and unlike the insurance-period fetch (which runs after the
  DB write already landed), this one runs *before* any write, so a failure
  here blocks the toggle entirely rather than merely under-recording an audit
  row. Not fixed in this diff: wrapping it in a try/except that treats a fetch
  failure as "flag off" would itself violate CLAUDE.md's "never silently
  swallow a DB error" rule, and changing the error-handling philosophy for
  all three call sites is a larger, separate decision than this PR's scope.
  Disclosed here per `spinr-dispatch-reviewer`'s finding rather than left
  unstated.

## 5. User-experience effect

**Driver-facing, and visible mid-session — this is the whole change.** A
driver who holds a live batch-dispatch offer and taps "Go offline" will, once
the flag is on, see a 409 with the message *"You have a pending ride offer.
Please accept or decline it before going offline."* instead of the toggle
succeeding.

This is a **new rejection of a previously-allowed action** — exactly the case
CLAUDE.md gate #3 requires a flag for. The message names the actual reason
and the two ways to clear it (accept or decline), rather than a generic
error. No rider, corporate-admin, or internal-admin surface changes.

**Not yet reviewed against the customer-centric tone standard by a human** —
the copy is a first draft. Flagging this explicitly rather than asserting it
meets the standard.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/status.py` | New flag-gated `ride_offers` check inside the Go Offline guard | Close the enforcement gap the guard's own comment already claimed |
| `backend/schemas.py` | New `go_offline_live_offer_guard_enabled: bool = False` | Make the flag flippable via admin dashboard, no redeploy |
| `backend/tests/test_go_offline_live_offer_guard.py` | New — 6 tests: both flag states, a stale-offer case, and the idempotent-reassert case found in review | Regression floor |

## 7. Before / after

```python
# Before
if not is_online:
    active_ride = ...  # queries `rides` only
    if active_ride:
        raise HTTPException(status_code=409, detail="Cannot go offline during an active trip...")
    # <-- a driver holding a live ride_offers claim falls through here, uncaught
```

```python
# After (flag ON; OFF is the block above, verbatim)
if not is_online:
    active_ride = ...  # unchanged
    if active_ride:
        raise HTTPException(status_code=409, detail="Cannot go offline during an active trip...")

    if bool(driver.get("is_online")):  # only on a genuine flip, not an idempotent re-assert
        _offline_guard_settings = await _get_offline_guard_settings()
        if bool(_offline_guard_settings.get("go_offline_live_offer_guard_enabled", False)):
            _pending_offline_offers = await db_supabase.get_rows(
                "ride_offers", {"driver_id": driver_id, "status": "pending"}, limit=5,
                columns="id,offered_at,ride_id",
            )
            if _fresh_pending_offers(_pending_offline_offers):
                raise HTTPException(status_code=409, detail="You have a pending ride offer...")
```

**Corrected during review.** The first draft ran the settings fetch and
`ride_offers` check unconditionally, including on an idempotent re-assert
(driver already offline in the DB re-tapping "Go offline"). `spinr-dispatch-
reviewer` flagged this as a driver-stuck scenario: a stale/orphaned
`ride_offers` row is invisible to the driver and they cannot clear it, so
409-ing a no-op toggle on one would strand them. The `bool(driver.get(...))`
guard above — equivalent to `status_flipped` for this branch, computed from
data already in scope rather than moving that later computation earlier —
fixes it.

**Concrete scenario.** Driver `drv-1` is online and idle. Batch dispatch
offers them ride `ride-7`. Two seconds later the driver taps Go Offline.

| | Result |
|---|---|
| Before / flag OFF | Toggle succeeds; driver goes offline holding a live offer |
| After, flag ON | 409; driver must accept or decline `ride-7` first |

## 8. Rollback plan

**Flip `go_offline_live_offer_guard_enabled` to `false`** via the admin
dashboard. No redeploy, no migration, no data cleanup. The flag-OFF path is
the pre-change code verbatim, pinned by `TestFlagOffIsUnchanged`, so rollback
returns to a tested state, not an unknown one.

No data is written by this change (it only ever raises before any write), so
there is nothing to remediate on rollback.

## 9. Verification performed

- [x] **Automated tests (unit).** 6 tests (2 added after review: stale-offer,
      idempotent-reassert). Full related-suite run: 134 passed (this file +
      the 5 sibling insurance/status suites).
- [x] **Mutation-verified.** 4 mutations planted — flag check inverted, the
      fresh-offers check dropped, the status filter changed from `pending` to
      `accepted`, and (added after the review fix) the flip-gate dropped
      entirely. All 4 caught. The status-filter one **initially survived**
      because the test's fake DB ignored the filter dict entirely; the fake
      was tightened to respect both `status` and `driver_id` (the second per
      the reviewer's own finding) before re-verifying. Recorded here rather
      than silently fixed.
- [x] **Blast-radius grep.** Confirmed this guard has exactly one call site
      (`update_driver_status`); no other function references it.
- [x] **Reviewed against CLAUDE.md:** release gate #3 (new flag, separate from
      #5571's, because it changes allowed behaviour not just an audit
      record); "Driver online/available flags" convention (unaffected — this
      guard runs before `is_available` is touched); dual-import pattern.
- [x] **Reviewer agent run against the diff** (`spinr-dispatch-reviewer`,
      CLAUDE.md gate 10) — verdict: no blockers, SAFE TO MERGE as a
      dark-shipped default-False flag. It confirmed the gap is real (traced
      `migrations/100_batch_dispatch.sql` and `routes/rides/matching.py`
      directly), that flag-OFF is byte-identical, and that `_fresh_pending_offers`
      is reused rather than reimplemented. It found two real gaps, both
      addressed above: the idempotent-reassert false-positive (fixed in code)
      and the undisclosed settings-load failure mode (disclosed in §4,
      unfixed — see the reasoning there). It also flagged three test-fidelity
      nits — missing `driver_id` filter assertion, no stale-offer test, one
      stale comment referencing a since-renamed test — all fixed.
- [ ] **Manual repro in staging — NOT done.** See below.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single boolean; OFF path is tested)
- [x] Blast radius is stated, not assumed (one call site, confirmed by grep)
- [x] User-experience effect is stated explicitly (§5) — this is not a silent
      change; it is a new, flagged rejection of a previously-allowed action

## 11. What was NOT verified

- **Not reproduced against a live driver session or real Supabase.** Unit-tier
  only, mocked Supabase.
- **The flag has not been exercised in staging or canary.** Dark-shipped
  precisely so it can be, before anyone flips it on.
- **The rejection message has not been reviewed by anyone for tone/clarity**
  against a real support-ticket volume this could generate. If false-positive
  409s turn out to be common (e.g. an offer the driver believes already
  expired but is still "fresh" per `STALE_PENDING_OFFER_SECONDS`), that would
  surface as driver confusion or support load — nothing in this change
  measures that risk; it can only be assessed once the flag is live in a
  canary.
- **Interaction with the driver app's own UI state was not examined.** If the
  app doesn't already surface a "you have a pending offer" banner when this
  409 fires, the error may look opaque to the driver. Not a rider-app/
  driver-app change — reasoning from the backend contract only.
- **No production query estimating how often a driver currently goes offline
  while holding a live offer.** That would size real-world impact before
  flipping the flag; it needs DB access this session does not have.
