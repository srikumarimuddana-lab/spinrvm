import { RideStatus } from '../constants/rideStatus';

// Canonical ride-status set — the single source of truth is
// backend/models/ride_status.py (RideStatus enum), mirrored by the
// shared/types/api/ride.ts `RideStatus` union. The rider app must expose
// EXACTLY these values: no missing status (e.g. 'scheduled', which the backend
// emits for scheduled rides) and no phantom status (e.g. 'failed', which the
// backend never emits for a ride). Drift here means the rider app either fails
// to recognise a real status or carries dead code that masks contract bugs.
const CANONICAL_RIDE_STATUSES = [
  'scheduled',
  'searching',
  'driver_assigned',
  'driver_accepted',
  'driver_arrived',
  'in_progress',
  'completed',
  'cancelled',
];

describe('rider-app ride-status contract', () => {
  it('exposes exactly the canonical backend ride-status set', () => {
    const riderStatuses = new Set(Object.values(RideStatus));
    expect(riderStatuses).toEqual(new Set(CANONICAL_RIDE_STATUSES));
  });
});
