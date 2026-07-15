import { RideStatus } from '../constants/rideStatus';

// Canonical ride-status set — the single source of truth is
// backend/models/ride_status.py (RideStatus enum), mirrored by
// shared/types/api/ride.ts and rider-app/constants/rideStatus.ts. The driver
// app must expose EXACTLY these values so its status handling can't drift from
// the backend contract (previously the driver app had no canonical enum and
// compared raw status literals inline across several files).
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

describe('driver-app ride-status contract', () => {
  it('exposes exactly the canonical backend ride-status set', () => {
    const driverStatuses = new Set(Object.values(RideStatus));
    expect(driverStatuses).toEqual(new Set(CANONICAL_RIDE_STATUSES));
  });
});
