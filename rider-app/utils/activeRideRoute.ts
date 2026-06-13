/**
 * Maps an active ride status to the screen that owns it. Focus guards
 * (home tab, AI chat) redirect through this so no other screen can sit on
 * top of a live ride — same routing as the cold-start/foreground resume
 * logic in app/_layout.tsx.
 */
export function activeRideRouteFor(status: string | null | undefined): string | null {
  switch (status) {
    case 'searching':
    case 'driver_assigned':
    case 'driver_accepted':
      return '/driver-arriving';
    case 'driver_arrived':
      return '/driver-arrived';
    case 'in_progress':
      return '/ride-in-progress';
    default:
      return null;
  }
}
