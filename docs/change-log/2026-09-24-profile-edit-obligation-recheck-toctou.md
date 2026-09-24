# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (opened alongside this file) |
| Related issue or gap ID | #5747 (finding 1) |

## 1. Issue / gap identified

`routes/drivers/profile.py`'s vehicle/document-edit mid-trip guard (#5739, 2026-09-23) checks
`has_active_ride_obligation()` once, then does real async work (an availability-mode lookup,
PII encryption) before acting on that snapshot to force the driver offline and record
insurance Period 0. A driver could be assigned a new ride by dispatch in that gap, and this
path would then close the brand-new Period 2 it never saw and record Period 0 instead —
asserting personal-auto-only coverage over an obligated, en-route driver.

## 2. Root cause

The obligation check and the write it gates were separated by two real awaits
(`driver_availability_v2_enabled()` and `_encrypt_driver_pii(updates)`), each a window where
dispatch's `claim_driver_atomic` could open Period 2 for this same driver via a concurrent
request. A snapshot read that old is exactly the class of bug #5739 itself was fixing (the
original version had no check at all); this is the same shape, just narrower.

## 3. Fix / remediation

Moved the `has_active_ride_obligation()` re-check to run immediately before the
`drivers` table UPDATE — after PII encryption, with no further awaits in between. The
availability-mode lookup (unrelated to the obligation question, needed only to decide the
V2-vs-legacy write shape) stays where it was; only the safety-critical obligation check moved.
No behavior change to the decision logic itself — same three outcomes (force offline + Period 0
/ defer to ride-end release / lookup failed → defer), just checked as close to the write as
async Python allows without a DB-level compare-and-set.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — one function (`update_my_driver` in `routes/drivers/profile.py`).
  Grepped for other callers/importers of `has_active_ride_obligation`: only this site and
  `utils/spinr_pass.py::force_offline_if_exhausted` (already checked — its own obligation check
  already sits directly before its write, no intervening awaits, confirmed by reading the
  current file; no change needed there).
- **What else reads/writes the same fields**: `drivers.is_online`/`is_available` (dispatch,
  go-online/offline toggle) and `driver_insurance_periods` (append-only, read by
  `close_period_after_release` and the reconciler). Neither is touched differently by this
  fix — same fields, same values, just decided later in the function.
- Does not change `record_vehicle_changes`' before/after snapshot: it diffs `updates` (the
  pre-encryption dict, unaffected by this reordering) against `TRACKED_FIELDS`
  (vehicle/document fields only, never `is_online`/`is_available`).

## 5. User-experience effect

None visible to the rider or a driver not actively editing their vehicle/document fields.
For a driver who edits those fields, the response and needs_review flip are unchanged; the
only behavior difference (by design) is a driver who becomes obligated to a ride in the
narrow gap between the old check point and the write now correctly keeps their Period 2/3
coverage and does not get forced offline — a bug fix, not a new user-facing flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/profile.py` | Moved the obligation re-check from before PII encryption to immediately before the `drivers` UPDATE | Close the TOCTOU gap #5747 found |
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | Added `test_obligation_check_runs_immediately_before_the_write`, asserting call order `encrypt → obligation_check → update_one` | Regression-guard the fix |

## 7. Before / after

```python
# Before
_obligated = await _deps.has_active_ride_obligation(driver["id"])
if _obligated is False and not _availability_v2_for_review:
    updates["is_online"] = False
    updates["is_available"] = False
    _forced_offline_for_review = True
...
result = await db_supabase.update_one(
    "drivers", write_filter, await _shared._encrypt_driver_pii(updates)
)
```

```python
# After
encrypted_updates = await _shared._encrypt_driver_pii(updates)
if changed_vehicle and driver.get("status") == "active":
    _obligated = await _deps.has_active_ride_obligation(driver["id"])
    if _obligated is False and not _availability_v2_for_review:
        encrypted_updates["is_online"] = False
        encrypted_updates["is_available"] = False
        _forced_offline_for_review = True
    ...
result = await db_supabase.update_one("drivers", write_filter, encrypted_updates)
```

## 8. Rollback plan

`git revert` — no migration, no data backfill, no money/wallet/ride-state mutation.
Reverting restores the wider (but still real, pre-existing before this fix) race window;
nothing stored needs remediation either way, since a period-0 row written in that window is
append-only and gets closed normally by the next ride-end release once the trip finishes.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_drivers_extended.py tests/test_drivers_shared_status_profile_coverage.py tests/test_spinr_pass_quota.py tests/test_insurance_period_close_helpers.py tests/test_insurance_period_forced_offline_sites.py tests/test_insurance_period_release_sites.py` — 316/316 pass
- [x] Blast-radius grep: every caller of `has_active_ride_obligation`, every reader/writer of `is_online`/`is_available` in this function
- [x] Reviewed against CLAUDE.md's insurance-period conventions (Period 2 starts at assignment, append-only, never guess "safe to disrupt")
- [ ] Manual repro in staging — not available from this session
- [ ] Feature-flagged — not applicable; this is a same-behavior timing fix to an already-shipped guard, nothing new to dark-ship

## 10. Sign-off

- [x] Rollback plan is concrete (`git revert`, no data cleanup)
- [x] Blast radius stated: isolated to one function, both `has_active_ride_obligation` call sites checked
- [x] No silent behavior change to a shipped flow without the UX field filled in — §5 states the one intended behavior difference explicitly

## What was NOT verified

Not tested against real Postgres/Supabase — only `mock_supabase_client`-mocked unit tests,
this repo's standard tier for this kind of change. The TOCTOU window is narrowed to the
theoretical minimum for cooperative-concurrency Python (back-to-back awaits with no
synchronous work between the check and the write) but is not eliminated — a true fix would
need a DB-level compare-and-set (e.g. the UPDATE's own WHERE clause verifying no open
Period 2/3 row exists), which is a larger change than this narrow-the-window fix and was not
built here.
