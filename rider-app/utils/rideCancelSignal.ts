/**
 * A ride_cancelled socket event or push should leave the screen only when
 * it is about the ride the rider is on right now. A late cancel for the
 * previous ride must not send a new search home.
 */
export function shouldLeaveScreenForRideCancelled(
  rideId: string | undefined,
  currentRideId: string | undefined,
  clearedRideId: string | null,
): boolean {
  if (rideId && clearedRideId === rideId) return false;
  if (!currentRideId) return false;
  if (rideId && rideId !== currentRideId) return false;
  return true;
}
