// Runtime lookup object — used throughout the app as RideStatus.COMPLETED etc.
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

// Convenience alias kept for backwards compatibility.
export type RideStatusType = typeof RideStatus[keyof typeof RideStatus];
