# Insurance-period classification fixes on the driver online/offline toggle

**Date:** 2026-08-19
**Surface:** backend drivers/safety (live-tested), 1 commit (`8c4d55f`)
**Trigger:** `spinr-insurance-period-auditor` pass on the tracking-overhaul batch surfaced two pre-existing blockers in `routes/drivers/status.py` — not introduced by the overhaul, but squarely in its domain (SGI period misclassification is a regulatory and insurance liability).

## Issue/gap identified
(1) The go-offline active-ride guard listed `driver_accepted`/`driver_arrived`/`in_progress` but omitted `driver_assigned`: a driver whose ride had just been assigned (offer on screen, already obligated — Period 2 starts at assignment per CLAUDE.md) could go offline without a 409, dropping their insurance classification to Period 0 (personal auto only) during the exact window the audit table calls out. (2) Any offline→online flip wrote `record_period_transition(driver_id, 1)` unconditionally — even when the driver still had an active ride (app relaunch mid-trip, admin force-offline undone) — opening Period 1 (contingent) while a passenger or assignment demanded Period 2/3 (primary commercial). This was also the recovery path the stale-P3 closer's design relies on ("the next go_online transition heals the no-open-row state"), which did not hold with a busy ride.

## Root cause
The offline guard's status list and the online-path's busy-ride list were written independently and drifted; the period write was added later gated only on `status_flipped`, never consulting the busy-ride lookup computed three lines earlier.

## Fix/remediation
The offline guard now uses the same four-status active list as the online path (`driver_assigned` included). The go-online period write is busy-ride-aware: `in_progress` → Period 3 with `ride_id`; `driver_assigned`/`driver_accepted`/`driver_arrived` → Period 2 with `ride_id`; rideless flips unchanged (1/0). `_busy_ride_row` is captured from the existing lookup — no extra query.

## Risk & impact on existing functionality
- **Blast radius**: `update_driver_status` is the only writer changed. Consumers of its effects: dispatch (`is_available` computation — unchanged), presence (unchanged), `record_period_transition` (existing RPC, migration 253 — its close-and-open semantics and the one-open-row partial unique index absorb the new period values with no schema change), driver-app Go Online/Offline buttons (see UX below). Grepped all `record_period_transition` call sites — ride_flow.py, users.py, admin/rides.py untouched and consistent.
- **Append-only contract**: intact — transitions still go through the RPC; no direct row mutation added.
- **State machine**: no ride-status writes; the 409 shape and message are the ones already shipped.

## User experience effect
Driver-visible, mid-session: a driver with a **just-assigned** ride who taps "Go offline" now gets the existing "Cannot go offline during an active trip" error instead of silently going offline. During the ~15 s offer window the correct action (decline the offer) is unaffected. No rider/admin-visible change. Period corrections are audit-trail-only.

## Before/after
```
before: ride driver_assigned  → "Go offline" succeeds → period row: 0 (personal auto)
after:  ride driver_assigned  → "Go offline" → 409; classification stays Period 2

before: go-online with in_progress ride → period row: 1 (contingent)
after:  go-online with in_progress ride → period row: 3 + ride_id (primary commercial)
```

## Rollback plan
`git revert 8c4d55f` — behavior-only change, no schema or data migration; already-written period rows are append-only audit data and stay valid either way.

## Verification performed
- New suite `test_driver_status_insurance_periods.py` (7 tests: 409 for all four active statuses incl. the previously-missing `driver_assigned`; period 3+ride_id for in_progress; period 2 for accepted; rideless 1/0 unchanged).
- All status-adjacent suites (`-k "status or go_online or availability or presence"`): 623 passed.
- Full fast backend suite run before push.

## What was NOT verified
- Not exercised against live Supabase or a real device; the driver-app's handling of the (already-shipped) 409 during the assignment window was reasoned about from existing decline-flow code, not manually reproduced on a device.
