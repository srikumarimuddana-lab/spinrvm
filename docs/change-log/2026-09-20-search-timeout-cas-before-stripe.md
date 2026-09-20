# Change Impact & Risk Log — `ride_search_timeout` claims the cancel before touching Stripe

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code session (2026-09-20 full-repo review, Phase 0 item C2) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch / rides / payments |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review, 🚨 C2 |

## 1. Issue / gap identified

The 5-minute "no drivers found" timer (`routes/rides/matching.py::ride_search_timeout`) could cancel a ride a driver had **just accepted**. For non-scheduled rides it checked the in-memory `status == searching`, then awaited `cancel_authorization()` — a live Stripe round-trip — and only then wrote `CANCELLED` through `update_ride()`, which filters on `id` only. A driver accepting in that window (accept_ride's CAS on `status='searching'` succeeds) was overwritten to `cancelled` with the rider's pre-auth hold already released: driver en route to a "cancelled" ride whose fare could never be captured, insurance Period 2 never closed, `ride_cancelled` pushed to a rider who had a driver.

## 2. Root cause

Two branches with different write discipline. The scheduled branch already did a compare-and-swap (`update_one` with `{"id", "status": "searching"}` and `return` on zero rows). The common branch predated it and was never converted; the TOCTOU window was widened from microseconds to a full network call when the WS-8 hold release was inserted between the read and the write.

## 3. Fix / remediation

Both branches now share one path: CAS-claim the cancel first (`update_one("rides", {"id", "status": "searching"}, …)`; zero rows → log at info and return, nothing else happens), **then** release the hold via the existing `utils/card_hold_release.release_open_hold` (the same helper the scheduled branch, the stuck-ride sweeper and the orphaned-hold reconciler already use), then the metric/WS/push as before. The migration-38 attribution fallback (retry without `cancelled_by` / `cancellation_type` on PGRST204) is preserved and now also keeps the CAS filter.

**Alternative considered:** keep the inline `_deps.cancel_authorization` call and just move the `update_ride` above it with a CAS. Rejected — that path also wrote `auth_status='released'` unconditionally, even when Stripe failed, hiding a still-live hold from `utils/orphaned_hold_reconciler.py` (which selects cancelled rides with an *open* `auth_status`). `release_open_hold` marks released only on success, so unifying on it fixes that latent bug for free and removes a second copy of the hold-release logic.

## 4. Risk & impact on existing functionality

Blast radius (grepped):

| Reader / writer | Effect |
|---|---|
| `ride_search_timeout` (in-process timer spawned by `create_ride`) | **Changed** — CAS first, hold release via shared helper. |
| `utils/stuck_ride_sweeper.py` (durable backstop for the same cancel) | untouched; already uses an atomic claim + `release_open_hold`. Behaviour now identical between timer and sweeper. |
| `routes/drivers/ride_flow.py::accept_ride` | untouched; its CAS on `status='searching'` is what now wins or loses cleanly against this timer. |
| `utils/orphaned_hold_reconciler.py` | benefits: a failed release leaves `auth_status` open, so the reconciler can see it (previously masked as `released`). |
| `spinr_card_hold_release_total` metric | new `source="search_timeout"` label value alongside the existing `scheduled_timeout`/`sweeper`/`reconciler`. Dashboards filtering on `source` gain a series; none break. |
| `spinr_rides_state_transition_total{to_status=cancelled}` | unchanged — still incremented once per successful claim. |
| Insurance periods | no direct writes here; the fix prevents the Period-2-never-closed corruption described in §1. |

Ride state machine: the only transition written is `searching → cancelled`, now guarded by the CAS the state-machine convention requires (`_require_ride_in_state()`'s equivalent for a background writer). No new states. Money: no new Stripe calls; the same `PaymentIntent.cancel` with the same idempotency key, now behind the claim.

Regression risk: a ride that legitimately times out is still cancelled (claim returns the row). If `update_one` returns `None` for a reason other than "status moved" (e.g. `supabase` client unavailable → `_write_skipped` returns `None`), the timer now silently no-ops instead of cancelling; the stuck-ride sweeper still cancels it within ~60 s, which is the documented durable backstop. Tests that drove this path through `update_ride` were updated (see §6).

## 5. User-experience effect

- **Rider:** no longer receives a "no drivers available" cancellation for a ride that was just accepted. Otherwise identical copy and timing.
- **Driver:** no longer ends up assigned to a ride the system marked cancelled.
- **Admin:** cancellation attribution (`cancelled_by='system'`, `cancellation_type='no_drivers_found'`) unchanged.
- Not visible mid-session except as the absence of the race.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | `ride_search_timeout`: single CAS claim for both branches before the hold release; hold release through `release_open_hold`; `auth_status` no longer written by the cancel itself | close the accept-vs-timeout race; stop masking failed releases |
| `backend/tests/test_p0_ship_blockers.py` | timeout tests now patch `update_one` and assert the CAS filter; new `test_timeout_is_a_noop_when_accept_wins_the_claim_race`; hold-release tests assert claim → Stripe → mark order and that a failed release is not marked released | regression pins |
| `backend/tests/test_coverage_rides.py` | the two `ride_search_timeout` tests assert on `update_one` (CAS) instead of `update_ride` | same |
| `backend/tests/test_preauth_release_on_cancel.py` | `TestSearchTimeoutReleasesHold` no longer greps `matching.py` for the relocated `"authorized"`/`"fare_only"`/`"released"` literals; pins `release_open_hold` wiring, claim-before-release order, and that the timer no longer pre-marks `released` | found by `spinr-dispatch-reviewer` — would have been red in CI |
| `docs/change-log/2026-09-20-search-timeout-cas-before-stripe.md` | this file | |

## 7. Before / after

```python
# Before (non-scheduled ride)
if current_ride.get("status") == RideStatus.SEARCHING:
    ...
    elif _booking_pi and _auth in ("authorized", "fare_only"):
        _released = await _deps.cancel_authorization(...)      # live Stripe call
    base_update = {"status": CANCELLED, ...}
    if _booking_pi and _auth in (...):
        base_update["auth_status"] = "released"                # even if Stripe failed
    await _deps.db_supabase.update_ride(r_id, {**base_update, ...})   # WHERE id = r_id
```

```python
# After (both branches)
if current_ride.get("status") == RideStatus.SEARCHING:
    claim_filter = {"id": r_id, "status": RideStatus.SEARCHING}
    claimed = await _deps.db_supabase.update_one("rides", claim_filter, {**base_update, ...})
    if not claimed:
        return                                                  # accept_ride won — no-op
    await release_open_hold(current_ride, source=...)           # marks released only on success
```

Scenario: rider books at t=0 with a card hold; no driver for 299.9 s; driver taps Accept at t=299.95 s (CAS `searching→driver_assigned/accepted` succeeds); timer fires at t=300 s. Before: hold released, ride overwritten to `cancelled`, rider pushed "no drivers". After: timer's CAS returns 0 rows → returns; hold stays for capture at trip end; driver proceeds.

## 8. Rollback plan

Code-only, no data migration, no flag. `git revert` + deploy restores the previous behaviour; no live-data state depends on the new ordering (the same DB columns are written with the same values on the happy path). Rides cancelled under the new code are indistinguishable from rides cancelled under the old code.

## 9. Verification performed

- `ruff check` + `ruff format` clean; `py_compile` clean.
- Existing scheduled-branch test `tests/test_scheduled_timing_guards.py::test_timeout_claim_precedes_hold_release` already pins claim-before-release and `update_ride` never awaited — still valid, unchanged.
- Adversarial re-read: the `except Exception` fallback path re-issues `update_one` with the same CAS filter (a schema fallback cannot reopen the id-only overwrite); `release_open_hold` never raises; the guest-SMS and metrics code after the claim is unchanged.
- `spinr-dispatch-reviewer` run against the diff before commit: state machine, sweeper/accept_ride interaction (row-level CAS means exactly one writer wins; no double hold release — `cancel_authorization` carries its own Stripe idempotency key), WS-once-on-claim all verified clean; its one blocker (the static-scan test above) fixed; its warning that `update_one → None` also covers the `_write_skipped` no-client case is reflected in the log line and is covered by the sweeper backstop.
- The static-scan assertions in `test_preauth_release_on_cancel.py` were simulated directly with the interpreter against the new sources (pytest unavailable) and hold.

## 10. What was NOT verified

- **pytest was not run in this session** (sandbox cannot reach PyPI). CI is the gate: `test_p0_ship_blockers.py::TestNoDriversAvailableTimeout`, `test_coverage_rides.py`'s two timeout tests, `test_preauth_release_on_cancel.py::TestSearchTimeoutReleasesHold`, and `test_scheduled_timing_guards.py` must all be green before merge.
- Not exercised against a real Supabase: `update_one`'s zero-row → `None` contract was read from `repositories/_base.py::_single_row_from_res`, not observed live.

---

## 11. Revision after PR review (2026-09-20)

Review on [#5599](https://github.com/srikumarimuddana-lab/spinrvm/pull/5599) found that treating a falsy `update_one` result as conclusively "another writer won" is unsafe. Verified against the code and fixed.

`update_one` calls `run_sync(_fn)` **without** a `retry_policy`, so it inherits the default `"read"` policy — `_BACKOFFS_BY_POLICY["read"] == [0.5, 1.5]`, i.e. 3 attempts — on a non-idempotent write. The transient classifier that gates those retries explicitly covers `ConnectionTerminated`, `RemoteProtocolError` / "Server disconnected", httpx timeouts, the H2 stream race and the httpx network-error family: exactly the *server committed, client never saw the response* class (CLAUDE.md calls the H2 GOAWAY retry expected, not hypothetical).

So: attempt 1's `UPDATE … WHERE id=… AND status='searching'` commits, its ack is lost, the retry re-runs the same CAS, legitimately matches **zero** rows, and returns a clean `None` — on a ride this call just cancelled. The early return then skipped the hold release, the `spinr_rides_state_transition_total{to_status=cancelled}` metric, the rider `ride_cancelled` WS event, the push and the guest SMS. The rider's app sits on "searching" for a cancelled ride and the cancellation-rate KPI undercounts. Money is not at risk — `utils/orphaned_hold_reconciler.py` sweeps cancelled rides whose `auth_status` is still open — so this is stale UI plus a metric gap.

This was **newly reachable on the high-volume path** because of this change: the old non-scheduled branch used an id-only `update_ride`, where a retry was an idempotent re-write. The scheduled-only CAS had the same shape, so the pattern is not new, but this extended it to every search timeout.

**Fix:** on a falsy claim, re-read the ride and check whether the row carries *this timer's own* attribution (`status='cancelled'`, `cancelled_by='system'`, `cancellation_type='no_drivers_found'`). If it does, the write was ours — a lost ack, not a lost race — so fall through to the side effects and log at `warning`. Otherwise no-op as before. Same shape `accept_ride` already uses (`routes/drivers/ride_flow.py`), chosen over passing `retry_policy="write"` because that helper has many callers and this keeps the change local.

Also noted and applied: the no-op log line now says "left 'searching' first, or the write was skipped" rather than asserting a race, since `update_one` also returns `None` when no Supabase client is configured.

**Not covered by the existing tests** — `test_timeout_is_a_noop_when_accept_wins_the_claim_race` mocks a clean `None`, and the attribution-fallback test raises *before* any write; neither simulates a committed-then-lost ack. A test for it needs `get_ride` to return the system-cancelled row after a falsy claim.
