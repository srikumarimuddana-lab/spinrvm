# Dimension 07 — State Machine & Dispatch

**Question:** Can the system enter an illegal state? Can two actors conflict and both succeed?

---

## Checklist

### State Transition Guards
- [ ] Every endpoint that changes state checks the current state before proceeding
- [ ] Invalid transitions return a clear error (not silently ignored)
- [ ] State transition constants are defined as named sets (e.g. `ARRIVE_FROM_STATES`)
- [ ] No endpoint accepts a transition from *any* state ("complete from any state" is always wrong)
- [ ] Client state machine matches server state machine exactly

### Race Condition Prevention
- [ ] Double-accept: ride acceptance uses atomic DB UPDATE with state precondition
  ```sql
  UPDATE rides SET driver_id=$1, status='driver_assigned'
  WHERE id=$2 AND status='searching'
  -- if 0 rows updated → ride already taken
  ```
- [ ] Double-dispatch: driver going offline between dispatch and claim is handled
- [ ] Concurrent state changes (e.g. driver cancels while rider cancels) produce a clean winner

### Cancellation Policies
- [ ] Rider cannot cancel after driver has arrived (`driver_arrived` state) without fee
- [ ] Driver cannot cancel after trip is in progress (`trip_in_progress` state)
- [ ] Cancellation fee is calculated and recorded — not commented out
- [ ] Cancelled ride clears driver assignment atomically

### Dispatch Algorithm
- [ ] Search radius defined and enforced
- [ ] Driver rating threshold enforced
- [ ] Dispatch algorithm selects from: nearest / highest_rating / longest_idle
- [ ] Re-dispatch after driver decline is handled (not fire-and-forget)
- [ ] 5-minute auto-cancel if no driver found — notification sent to rider

### OTP / Pickup Verification
- [ ] Pickup OTP generated fresh per ride
- [ ] OTP hashed before DB storage
- [ ] OTP verified at trip start — cannot start without correct OTP
- [ ] OTP verified at server — not client-side only

---

## Severity Guide

| Finding | Severity |
|---|---|
| Two drivers can both accept the same ride (no atomic guard) | CRITICAL |
| Complete ride accepted from any state — pickup OTP skipped | HIGH |
| Rider can cancel after driver arrived — driver gets no fee | HIGH |
| Driver can cancel in-progress ride — fare skipped | HIGH |
| Re-dispatch on decline is fire-and-forget (silent failure) | MEDIUM |
| Client state machine has extra states not in server | MEDIUM |
| Cancellation fee calculated but not charged | MEDIUM |
| State guard exists but error message doesn't explain why | LOW |
