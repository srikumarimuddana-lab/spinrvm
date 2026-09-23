# PR 5722 stop completion guard

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | PR 5722 / pending |
| Related issue or gap ID | PR 5722 driver navigation review |

## Issue / gap identified

The driver completion API could settle a trip while rider-requested intermediate stops were still pending.

## Root cause

`complete_ride` validated only ride status before beginning completion work and did not inspect persisted stop progress.

## Fix / remediation

`complete_ride` now rejects any uncompleted stop (and malformed legacy entries) with HTTP 409 before final location capture, route aggregation, or settlement work.

## Risk & impact on existing functionality

Blast radius: driver ride completion only. Other state transition, fare settlement, earnings, and background readers remain unchanged. Rides with remaining stops now require the driver stop-completion endpoint before the existing completion path proceeds. Rides with no stops or fully completed stops retain prior behavior.

## User-experience effect

Drivers can no longer end a ride before acknowledging every remaining stop; the panel will expose the explicit stop action. The response is additive and no payout behavior changes once all stops are complete.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_complete.py` | Added pre-side-effect pending-stop guard | Prevent stop bypass and settlement |
| `backend/tests/test_driver_stop_progress.py` | Added completion rejection regression | Ensure guard runs before completion work |
| `docs/change-log/2026-09-23-pr5722-stop-completion-guard.md` | Recorded risk and rollback | Live-tested change log |

## Before / after

```py
# Before
if ride.get("status") not in COMPLETE_FROM_STATES:
    raise RideStateError(...)
```

```py
# After
if any(not isinstance(stop, dict) or stop.get("completed") is not True for stop in stops):
    raise HTTPException(status_code=409, ...)
```

## Rollback plan

Revert the guard to restore prior completion behavior. Existing persisted stop metadata is optional JSON and can remain; no live data reversal is required.

## Verification performed

- [ ] Automated tests run (running before commit)
- [x] Guard placed before completion location capture and all completion side effects
- [x] No migration or wallet/Stripe behavior changed

## What was NOT verified

No staging settlement or live driver session was run; the guard is covered by mocked route tests only.
