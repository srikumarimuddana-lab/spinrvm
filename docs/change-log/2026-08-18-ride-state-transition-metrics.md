# Change Impact & Risk Log — ride-state-transition metrics (observability gap)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code session (see PR for attribution) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides / dispatch / observability |
| PR / commit link | (this commit) |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` finding N12b; `ACTION_ITEMS.md` A40 |

## 1. Issue / gap identified

No Prometheus metric existed for any ride-state transition after offer-acceptance (arrival, trip
start, completion, cancellation). The two headline KPIs CLAUDE.md names — match rate and rider/driver
cancellation rate — were consequently invisible to any dashboard or alert; the only way to see them
was a manual database query.

## 2. Root cause

The dispatch layer (pre-acceptance) already emits `spinr_dispatch_offer_sent_total` /
`spinr_dispatch_offer_accepted_total` (see `backend/tests/test_dispatch_metrics.py`), but that
instrumentation effort never extended past acceptance — every write site that flips `rides.status` to
`driver_arrived`, `in_progress`, `completed`, or `cancelled` only ever emitted a WS event and
sometimes a log line, never a counter increment.

## 3. Fix / remediation

Added one counter, `spinr_rides_state_transition_total{to_status=...}`, matching the existing
`spinr_payment_settlement_total{outcome=success|failed|retry}` label convention already documented in
CLAUDE.md's metric-naming section (one metric family, a label for the specific outcome/state — not a
separate metric name per state). Wired `_metric_inc(...)` (or `_deps._metric_inc(...)`, depending on
each file's existing import style) at every production-reachable write site:

| `to_status` | File | Function |
|---|---|---|
| `driver_arrived` | `routes/drivers/ride_flow.py` | `arrive_at_pickup` |
| `in_progress` | `routes/drivers/ride_flow.py` | `verify_pickup_otp` |
| `in_progress` | `routes/rides/lifecycle.py` | `rider_start_ride` (alternate rider-visible route to the same transition) |
| `completed` | `routes/drivers/ride_complete.py` | `complete_ride` (driver-initiated) |
| `completed` | `routes/rides/lifecycle.py` | `rider_complete_ride` (rider-initiated early-end) |
| `cancelled` | `routes/rides/cancellation.py` | `cancel_ride_rider` |
| `cancelled` | `routes/rides/cancellation.py` | `cancel_scheduled_ride` |
| `cancelled` | `routes/drivers/ride_cancel.py` | driver-initiated `cancel_ride` |
| `cancelled` | `routes/drivers/ride_cancel.py` | `mark_rider_noshow` |
| `cancelled` | `routes/rides/matching.py` | `ride_search_timeout` (auto-cancel, "no drivers found") |

**Deliberately NOT instrumented** (explicit scope boundary, not an oversight):
- `routes/rides/lifecycle.py::simulate_driver_arrival` — dev/test only, 403s in production.
- `routes/drivers/ride_flow.py::start_ride` — the no-OTP trip-start path, explicitly 410s in
  production ("Use POST /rides/{ride_id}/verify-otp to start a ride in production").
- `routes/drivers/subscriptions.py`'s subscription-expiry forced cancellations (3 sites) — a rarer,
  system-initiated bulk-operation path, not covered in this pass. Its absence means the cancellation
  counter is a **conservative undercount** for that specific cause, not a false signal — flagging this
  explicitly rather than letting the metric imply full coverage it doesn't have.

Each write site's metric call is placed immediately after that site's own atomic-claim success guard
(`if guard/claim/claimed is ...: raise/continue`), so it only increments once the transition actually
took effect — never on a race-lost or already-transitioned no-op. Care was taken not to double-count:
`cancellation.py::cancel_ride_rider` writes the ride row twice (the atomic cancel claim, then a
follow-up fee/attribution enrichment write) — only the first write is instrumented, since the second
is enrichment of an already-cancelled row, not a second transition.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), additive-only, isolated to 10 call sites across 6 files.**

- **What changed at each site**: exactly one new line, a fire-and-forget in-process counter increment
  (`utils/metrics.inc`, the same lightweight, per-process, lock-protected counter the dispatch metrics
  already use). No control flow changed, no new exception path introduced, no existing return value or
  side effect altered.
- **Callers/consumers of the touched functions**: none of the touched functions' external behavior
  changed — every existing test for `arrive_at_pickup`, `verify_pickup_otp`, `rider_start_ride`,
  `rider_complete_ride`, `complete_ride`, `cancel_ride_rider`, `cancel_scheduled_ride`, driver
  `cancel_ride`, `mark_rider_noshow`, and `ride_search_timeout` passes unmodified after this change
  (see §9) — the metric call is a pure addition with no observable effect on the function's return
  value, DB writes, or WS/push traffic.
- **`/metrics` endpoint**: no pre-registration needed — this codebase's counters are created on first
  `inc()` call and read generically by `utils/metrics.render_prometheus`, matching every other counter
  already in use (confirmed by grep: no metric name is pre-declared anywhere in this codebase).
- **Performance**: `utils/metrics.inc` is an in-process, lock-protected dict increment — negligible
  cost, same as the already-shipped dispatch counters on the same request paths, well within every
  touched endpoint's documented SLA budget.
- **Does NOT touch**: money/wallet code, the ride state machine's actual transition logic, WS payload
  shape, or any external-facing API contract. Purely additive observability.

## 5. User-experience effect

None. This is an internal metrics-only change — no rider, driver, corporate-admin, or internal-admin
screen changes behavior, timing, or copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_flow.py` | Added metric calls in `arrive_at_pickup` and `verify_pickup_otp` (2 sites) | `driver_arrived` / `in_progress` transitions |
| `backend/routes/rides/lifecycle.py` | Added metric calls in `rider_start_ride` and `rider_complete_ride` (2 sites) | `in_progress` / `completed` counterpart transitions |
| `backend/routes/drivers/ride_complete.py` | Added metric call in `complete_ride` (1 site) | `completed` transition (driver-initiated) |
| `backend/routes/rides/cancellation.py` | Added metric calls in `cancel_ride_rider` and `cancel_scheduled_ride` (2 sites) | `cancelled` transitions |
| `backend/routes/drivers/ride_cancel.py` | Added metric calls in driver `cancel_ride` and `mark_rider_noshow` (2 sites) | `cancelled` transitions |
| `backend/routes/rides/matching.py` | Added metric call in `ride_search_timeout` (1 site) | `cancelled` transition (auto-cancel, "no drivers found") |
| `backend/tests/test_ride_state_transition_metrics.py` | New file — 4 tests, one per distinct `to_status` label value, following the existing `test_dispatch_metrics.py` convention | Regression coverage for the new instrumentation |
| `CLAUDE.md` | Added `spinr_rides_state_transition_total{...}` to the documented metric-naming list | Keep the dashboards-facing metric reference accurate |
| `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` | Finding N12b marked FIXED with evidence | Keep the audit's own ledger accurate |
| `ACTION_ITEMS.md` | A40 annotated with the fix | Same ledger-accuracy requirement |

## 7. Before / after

```python
# Before — backend/routes/drivers/ride_flow.py, arrive_at_pickup:
    if guard is None:
        raise HTTPException(status_code=409, detail="Ride is not in driver_accepted state")

    if ride.get("rider_id"):
        await _deps.manager.send_personal_message(...)
```

```python
# After
    if guard is None:
        raise HTTPException(status_code=409, detail="Ride is not in driver_accepted state")

    _metric_inc("spinr_rides_state_transition_total", {"to_status": "driver_arrived"})

    if ride.get("rider_id"):
        await _deps.manager.send_personal_message(...)
```

(The remaining 9 sites follow the identical one-line pattern, placed after each site's own
atomic-claim success guard — see the file-by-file diff for exact placement.)

## 8. Rollback plan

**Code revert is sufficient.** This adds only in-process counter increments — no schema change, no
data written or mutated, no migration, no persisted-value change. Reverting the commit stops the new
counter from incrementing on the next deploy; nothing needs to be unwound. No feature flag was used:
observability instrumentation is not gated anywhere else in this codebase (the dispatch counters this
mirrors are unflagged too), and there is no meaningful "half-shipped" state to protect against — a
metric either increments correctly or (if reverted) simply doesn't exist, with no functional
difference to the request path either way.

## 9. Verification performed

- [x] Automated tests added and run: 4 new tests in `backend/tests/test_ride_state_transition_metrics.py`
  (`test_arrive_at_pickup_counts_driver_arrived`, `test_verify_pickup_otp_counts_in_progress`,
  `test_rider_complete_ride_counts_completed`, `test_cancel_ride_rider_counts_cancelled`) — one per
  distinct `to_status` label value, following the established `test_dispatch_metrics.py` pattern
  (real function call through mocked DB/WS/push, before/after counter delta via `metrics.snapshot()`).
- [x] Full regression sweep of every touched function's existing test suite:
  `test_driver_ride_flow_coverage.py`, `test_ride_accept_flow.py`, `test_e2e_ride_lifecycle.py`,
  `test_rides.py`, `test_cancel_fee_from_hold.py`, `test_cancellation_fee_card_charge.py`,
  `test_e2e_cancellation.py`, `test_p2_scheduled_rides.py`, `test_ride_cancellation_branches.py`,
  `test_ride_state_machine.py`, `test_rides_extended.py`, `test_c2_driver_cancel_atomic.py`,
  `test_fee_wallet_atomic.py`, `test_ride_complete_coverage.py`, `test_p0_ship_blockers.py`,
  `test_preauth_release_on_cancel.py` — 373 tests total, 0 failed.
- [x] Full backend suite run: `pytest backend/tests` (entire suite, no filter) — confirms no other
  test anywhere in the repo broke from the new metric calls.
- [ ] Manual repro steps followed in staging — **not performed**; no staging environment or live
  Prometheus scraper access in this session.
- [x] Blast-radius grep performed: every write site across `routes/rides/` and `routes/drivers/` that
  sets `rides.status` to `driver_arrived`/`in_progress`/`completed`/`cancelled`, cross-checked against
  which are production-reachable (not behind an `ENV == "production"` block) — the two dev-only
  exclusions and the one explicitly-deferred subscription-expiry path are both named in §3.
- [x] Reviewed against relevant CLAUDE.md conventions: metric naming (`spinr_<domain>_<metric>_<unit>`,
  reused the existing `{outcome=...}`-style label pattern rather than inventing four separate metric
  names); "state transitions → info log + metric" (this closes the metric half of that rule; the info
  log half was already inconsistent across these files before this change and is unchanged by it —
  not addressed here, out of this fix's scope).
- [ ] Feature-flagged — **not applicable**, see rollback-plan justification above.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (revert the commit; nothing stored to unwind)
- [x] Blast radius is stated, not assumed (full write-site grep + explicit exclusion list in §3/§4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — §5 states
  there is none; this is metrics-only, no functional or UX change at any of the 10 sites

## What was NOT verified

- Not exercised against a live Prometheus scraper or a real `/metrics` endpoint request — confirmed
  only via `utils/metrics.snapshot()` in unit tests that the counter accumulates correctly in-process.
- Did not instrument `routes/drivers/subscriptions.py`'s 3 subscription-expiry forced-cancellation
  sites — a deliberate scope decision (§3), not a gap discovered and left unaddressed by accident;
  the cancellation-rate metric will undercount by whatever volume that path represents until a
  follow-up closes it.
- Did not add the "info log" half of CLAUDE.md's "state transitions → info log + metric" rule at sites
  that were missing it before this change (several of the touched functions already had no
  `logger.info` for their transition, matching the audit's own observation) — this fix closes the
  metric gap specifically, as that was the audit's stated finding; the logging gap is a separate,
  narrower follow-up if wanted.
- Did not verify against a live/staging multi-replica deployment that per-process counters aggregate
  correctly at the Prometheus-scrape layer — relied on the existing, already-shipped
  `spinr_dispatch_offer_sent_total`/`accepted_total` counters' identical per-process design as
  precedent that this pattern works in production today.
