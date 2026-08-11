# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (session_01Wk3M9NdQJWqgpATtogSjD8) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch, rides |
| PR / commit link | branch `claude/n5-batch-dispatch-push-fallback` |
| Related issue or gap ID | ACTION_ITEMS.md N5 follow-up (extends PR #3551) |

## 1. Issue / gap identified

N5 (PR #3551, merged) added a push-notification fallback alongside the existing WebSocket message for the **assigned-driver** case in `routes/rides/cancellation.py`'s rider-cancel flow. The **batch-dispatch pending-offers** case in the same function — a driver with a pending offer on a ride that gets cancelled before any driver is assigned — still only sent a WebSocket message, with no push fallback. Found while investigating N5's scope for a possible follow-up; verified directly by grep (`send_push_notification` had exactly one call site in the file, not two) before making any change.

## 2. Root cause

N5's fix targeted the specific WS-notify block it was scoped to (the assigned-driver case, lines ~345-355). The batch-dispatch pending-offers loop (~403-415) is a structurally similar but separate code block later in the same function, notifying a different set of drivers (those with a pending, not-yet-accepted offer) via the same `manager.send_personal_message` pattern — it was never in scope for the original N5 fix and was missed.

## 3. Fix / remediation

Added a `send_push_notification` call inside the pending-offers loop, right after the existing `send_personal_message` WS call, for each pending-offer driver whose `user_id` resolves successfully. Mirrors the assigned-driver push exactly: same title/body copy ("Ride Cancelled" / "The rider cancelled this ride."), same `data={"type": "ride_cancelled", "ride_id": ...}`, same `priority="dispatch"` (bypasses opt-out, falls back to the retry queue on transient failure — a driver actively deciding whether to accept a since-cancelled offer is as time-sensitive as an offer itself), same `target_app="driver"`, backgrounded via the same `_deps.spawn()` fire-and-forget pattern so a slow FCM/Expo round-trip doesn't hold up the rider's own cancel response. The existing `try/except` around each per-driver notify already logs and continues on failure (`logger.warning(f"[CANCEL] failed to notify batch-offer driver {_offer_did}: {_e}")`) — the new push call sits inside that same guard, so a push failure is handled identically to a WS failure.

## 4. Risk & impact on existing functionality

Purely additive inside an already-isolated per-driver loop with its own try/except. Grepped every caller of `cancel_ride_rider` (the route function) — it's a FastAPI route, invoked only via the `/rides/{ride_id}/cancel` HTTP endpoint, no other internal callers. The batch-dispatch loop itself has exactly one other side effect per iteration (`set_driver_available`), untouched by this change. `send_push_notification` itself is unmodified — this is a new caller, not a signature/behavior change to the shared function, so no other consumer of it is affected.

Blast radius: isolated to the pending-offers notification loop in `routes/rides/cancellation.py`.

## 5. User-experience effect

Driver-facing, additive only. A driver with a pending offer on a ride the rider cancels before assignment now also receives a push notification (in addition to the existing WebSocket message) if their app is backgrounded. No existing behavior is removed — the WS message still fires exactly as before. Not visible mid-session to the rider; visible to an affected driver as a new "Ride Cancelled" push they wouldn't have received before.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/cancellation.py` | Added `send_push_notification` call (spawned) inside the batch-dispatch pending-offers per-driver loop | Close the same WS-only gap N5 fixed for the assigned-driver case, for the pending-offer case |
| `backend/tests/test_ride_cancellation_branches.py` | New test `test_batch_pending_offer_driver_also_gets_push_not_just_ws` | Pins the push fires with the correct `user_id`/`priority`/`target_app`/`data.type`, mirroring `test_e2e_cancellation.py`'s existing assigned-driver push test's assertion shape |
| `docs/change-log/2026-08-11-n5-batch-dispatch-push-fallback.md` | New Change Impact Log | Required per `CLAUDE.md` — touches the ride-cancellation flow |
| `ACTION_ITEMS.md` | N5 entry extended with this follow-up note | Tracking |

## 7. Before / after

```python
# Before
if _uid:
    await _deps.manager.send_personal_message(
        {"type": "ride_cancelled", "ride_id": ride_id, "reason": "Rider cancelled"},
        f"driver_{_uid}",
    )
```

```python
# After
if _uid:
    await _deps.manager.send_personal_message(
        {"type": "ride_cancelled", "ride_id": ride_id, "reason": "Rider cancelled"},
        f"driver_{_uid}",
    )
    _deps.spawn(
        _deps.send_push_notification(
            _uid,
            "Ride Cancelled",
            "The rider cancelled this ride.",
            data={"type": "ride_cancelled", "ride_id": str(ride_id)},
            priority="dispatch",
            target_app="driver",
        )
    )
```

## 8. Rollback plan

`git revert` — pure additive application code, no schema/data change, no migration, no feature flag applicable. Reverting restores the pre-existing WS-only behavior for the pending-offers case exactly.

## 9. Verification performed

- [x] Automated tests run — real venv, real `pytest`: `tests/test_ride_cancellation_branches.py` + `tests/test_e2e_cancellation.py` → **27 passed, 0 failed**. Broader sweep `pytest tests/ -k cancel` → **262 passed, 1 skipped, 0 failed**.
- [x] Blast-radius grep performed — see §4.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — WebSocket event / notification-delivery convention, background-loop/fire-and-forget spawn pattern already established by N5.
- [ ] Manual repro steps followed in staging — not performed, no staging environment available in this session.
- [ ] Feature-flagged — not applicable; purely additive, no existing behavior removed or changed.

## 10. What was NOT verified

- No live FCM/Expo push actually sent — verification is unit-level with `send_push_notification` mocked, per this repo's established testing convention (mirrors how N5's own assigned-driver push test verifies).
- No real production traffic exercised (real Stripe/Supabase, real multi-driver pending-offer scenario at scale).

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (n/a — purely additive, no existing behavior changed)
