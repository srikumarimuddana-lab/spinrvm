# P0 — Backend Critical: Fix Before Any Device Testing

These 2 backend items must be resolved before riders or drivers exercise the live API. One blocks every fare collection after a completed ride; the other crashes the driver-rating endpoint on the very first rating against any new driver.

Source audit: `reports/audits/2026-04-23-backend-api-v1.txt`
Branch: `claude/audit-continuation-batch-2`

**Estimated total effort:** ~3 hours (short fix), ~6 hours with the recommended enum refactor in B-P0-2.

---

## B-P0-1 · Driver Rating Endpoint Crashes 500 on First-Ever Rating

**What's wrong:** In `backend/routes/rides.py` the rating-update branch only assigns `average_rating` inside the `if rated_rides:` block (line 1796) but uses `average_rating` unconditionally below (lines 1801–1803). When a brand-new driver receives their first rating ever, `rated_rides` is empty, the branch is skipped, and the next reference raises `UnboundLocalError` → HTTP 500.

**File to fix:** `backend/routes/rides.py:1788,1796,1801–1803`

**How to fix:** Initialize `average_rating` to the driver's existing baseline before the conditional, then update inside the `if` block.
```python
average_rating = driver.get('rating', 5.0)  # baseline before the if-block
if rated_rides:
    average_rating = round(sum(...) / len(rated_rides), 2)
# safe to use average_rating below
```

**Regression test:** `test_first_rating_does_not_crash` — driver row has zero prior ratings, POST a rating, expect 200 and the driver's `rating` field to equal the submitted score.

**Why it matters:** Every onboarded driver will be rated once. Today, the *first* rating against any driver crashes — the rider sees an error, the driver never gets credit, and the support queue grows linearly with new-driver onboarding.

**Effort:** 1 hour
**Severity:** HIGH · **Risk score:** 24 · **Regulations:** SK-CPPA
**Audit ref:** Phase A · 01-1

---

## B-P0-2 · Rider Cannot Pay After Driver Completes the Ride (state-string mismatch)

**What's wrong:** `complete_ride` in `backend/routes/drivers.py:2088` writes `status='completed'`. The `process_payment` endpoint in `backend/routes/rides.py:1309,1312` guards with `if _ride_status != 'trip_completed': raise 409`. After every successful trip the rider hits payment, gets 409, and fare collection is blocked. This is the same family as DV-3 (`COMPLETE_FROM_STATES = ('in_progress',)` vs `'trip_in_progress'`), but a different concrete instance.

**Files to fix:**
- `backend/routes/rides.py:1309,1312` (process_payment guard)
- `backend/routes/drivers.py:2088` (complete_ride writer)

**How to fix:** Two-step.
1. **Short term (2 h):** make `process_payment` accept both `'completed'` and `'trip_completed'` so collection unblocks immediately.
2. **Recommended (4 h):** introduce a `RideStatus` enum (per DV-3) and sweep every ride-status string literal in the repo. The state-machine doc in `CLAUDE.md` already enumerates the canonical set — encode it once and reference everywhere.

```python
# backend/models/ride_status.py
class RideStatus(str, Enum):
    SCHEDULED = "scheduled"
    SEARCHING = "searching"
    DRIVER_ASSIGNED = "driver_assigned"
    DRIVER_ACCEPTED = "driver_accepted"
    DRIVER_ARRIVED = "driver_arrived"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
```

**Regression test:** `test_rider_payment_after_driver_completes` — driver completes ride, rider calls `process_payment`, expect 200 and a non-null `payment_intent_id`.

**Why it matters:** Every completed ride today fails fare collection. This is a P0 revenue-blocker and a CRITICAL CLAUDE.md state-machine violation. Risk score 48 (CRITICAL × org × certain).

**Effort:** 2 hours short fix; 4 hours for the enum refactor (recommended).
**Severity:** CRITICAL · **Risk score:** 48 · **Regulations:** PCI-DSS, SK-CPPA
**Audit ref:** Phase B · 07-1 (also referenced in `rider-phase-e-P0-issues.md`)

---

## Checklist

- [ ] B-P0-1 Initialize `average_rating` before the conditional in `routes/rides.py:1796`
- [ ] B-P0-2 Accept `'completed'` in `process_payment` (short fix) **and** plan the `RideStatus` enum sweep (DV-3)

## After this file

- Run `pytest backend/tests/test_ride_state_machine.py` and the new regression tests above.
- Move on to `backend-P1-before-beta.md` (10 HIGH/MED items including JWT length enforcement, Firebase audience binding, retention purge cron, ride-history pagination, supply-chain hash pinning).
