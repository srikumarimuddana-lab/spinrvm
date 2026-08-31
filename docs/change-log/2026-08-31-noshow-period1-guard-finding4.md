# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | fix/noshow-period1-guard-finding4 |
| Related issue or gap ID | #4597 Finding 4 (P3) |

## 1. Issue / gap identified

`mark_rider_noshow` (`backend/routes/drivers/ride_cancel.py:434-435` before this fix) writes
`driver_insurance_periods` Period 1 unconditionally after releasing the driver, without
checking whether the release actually succeeded — the same class of bug as #4597 Findings 1
and 3, already fixed in #4729/#4732.

## 2. Root cause

`set_driver_available` silently clamps `is_available -> False` when the driver has already
gone offline (e.g. raceable against the `subscription_expiry` background loop, or an admin
ban landing in the same window as a driver-initiated no-show). `matching.py` and
`cancellation.py`'s rider-cancel path both already guard this with
`released.get("is_available")` before writing Period 1 (the rider-cancel guard added in
#4729, 2026-08-31). The driver no-show path predates that pattern and was never updated to
match — flagged as Finding 4 in the original #4597 audit and explicitly left as separate
follow-up work at the time.

## 3. Fix / remediation

Capture `set_driver_available`'s return value and only call
`record_period_transition(driver_id, 1)` when `released.get("is_available")` is true —
identical guard shape to the Finding 1/3 fixes.

## 4. Risk & impact on existing functionality

Blast-radius grep across `backend/`: `record_period_transition` has 27 call sites across 16
files; `set_driver_available` has 21 callers across 13 files. This change touches exactly
**1** of those — `ride_cancel.py`'s `mark_rider_noshow` Period-1 write.

- **Blast radius: isolated.** No shared helper, schema, or background-loop registration
  changed.
- **Known adjacent gap, explicitly NOT touched by this PR:** `ride_cancel.py:218`
  (`cancel_ride`, the driver-initiated cancel handler — different function, same file) also
  calls `set_driver_available` unconditionally before its own Period-1 write, and does not
  check the release's `is_available` return either. It has a *different* existing guard (an
  explicit `ride.get("status") in (DRIVER_ASSIGNED, DRIVER_ACCEPTED, DRIVER_ARRIVED)` check)
  that Finding 4 as scoped by #4597 does not name — the original audit called out only lines
  434-435. Flagging this rather than silently folding it into this PR's diff, per the
  surgical-changes convention; worth its own follow-up issue if the same race is judged
  plausible there.
- No ride-state-machine write, and no money/wallet delta, is added or changed — only the
  condition under which the existing `driver_insurance_periods` write fires.

## 5. User-experience effect

None rider/driver-facing. Internal audit-trail write only (`driver_insurance_periods`); the
scenario this prevents (a driver already offline getting a false Period 1 row) has no
observable effect on the driver's own app state.

Not feature-flagged: bug fix aligning this path with an already-shipped, unflagged guard
pattern used at two other call sites (`matching.py`, `cancellation.py`).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_cancel.py` | Gate `mark_rider_noshow`'s Period-1 write on `set_driver_available`'s actual return value | Finding 4 — prevent false Period-1 write for an already-offline driver |
| `backend/tests/test_c2_driver_cancel_atomic.py` | 2 new tests: offline driver skips Period-1 write; online driver still gets it (regression guard); added missing `Decimal` import the new tests needed | Cover Finding 4's fix and its boundary |

## 7. Before / after

```python
# Before (ride_cancel.py, mark_rider_noshow)
await db_supabase.set_driver_available(driver["id"], True)
await _deps.record_period_transition(driver["id"], 1)
```

```python
# After
_noshow_released = await db_supabase.set_driver_available(driver["id"], True)
if isinstance(_noshow_released, dict) and _noshow_released.get("is_available"):
    await _deps.record_period_transition(driver["id"], 1)
```

## 8. Rollback plan

`git-revert-safe`. Pure code-path guard, no schema/migration/data change beyond the existing
append-only `driver_insurance_periods` table (never mutated, only conditionally
not-written). Reverting restores the prior (buggy) behavior exactly.

## 9. Verification performed

- [x] Automated tests: `pytest backend/tests/test_c2_driver_cancel_atomic.py` — 10/10 passed
  (2 new). `ruff check` clean on both changed files.
- [ ] Manual repro in staging — **not performed**, no Supabase access this session; mocked
  fixtures only, per this repo's standard unit-test convention.
- [x] Blast-radius grep performed: see §4.
- [x] Reviewed against CLAUDE.md's insurance-period invariants and append-only rule.
- [x] Feature-flag question addressed (§5) — not flagged, justified.

## 10. Sign-off

- [x] Rollback plan concrete and testable (git revert)
- [x] Blast radius stated, not assumed (§4)
- [x] No silent UX change without the field filled in (§5)

**What was NOT verified:** not exercised against real Supabase/staging. How often this race
actually fires in production (driver already offline by the time a no-show cancel lands) was
not measured. The adjacent `ride_cancel.py:218` gap noted in §4 was identified but
deliberately not fixed here — out of this PR's stated scope.
