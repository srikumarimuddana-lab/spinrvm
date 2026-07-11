// Canonical ride-status set for the driver app. Single source of truth is
// backend/models/ride_status.py; kept in sync with shared/types/api/ride.ts
// and rider-app/constants/rideStatus.ts. Guarded by
// __tests__/rideStatusContract.test.ts so it can't drift from the backend.
//
// Runtime lookup object — use as RideStatus.DRIVER_ASSIGNED etc. instead of
// hardcoding backend status literals inline.
export const RideStatus = {
  SCHEDULED: 'scheduled',
  SEARCHING: 'searching',
  DRIVER_ASSIGNED: 'driver_assigned',
  DRIVER_ACCEPTED: 'driver_accepted',
  DRIVER_ARRIVED: 'driver_arrived',
  IN_PROGRESS: 'in_progress',
  COMPLETED: 'completed',
  CANCELLED: 'cancelled',
} as const;

export type RideStatusType = typeof RideStatus[keyof typeof RideStatus];
