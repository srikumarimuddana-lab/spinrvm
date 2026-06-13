/**
 * Active-ride focus-guard routing. Pins the guardrail: every active status
 * maps to the screen that owns the ride, and only terminal/absent statuses
 * let the rider stay where they are (home tab, AI chat).
 */
import { activeRideRouteFor } from '../activeRideRoute';

describe('activeRideRouteFor', () => {
  it.each([
    ['searching', '/driver-arriving'],
    ['driver_assigned', '/driver-arriving'],
    ['driver_accepted', '/driver-arriving'],
    ['driver_arrived', '/driver-arrived'],
    ['in_progress', '/ride-in-progress'],
  ])('%s → %s', (status, route) => {
    expect(activeRideRouteFor(status)).toBe(route);
  });

  it.each(['completed', 'cancelled', 'scheduled', '', undefined, null])(
    'does not redirect for %s',
    (status) => {
      expect(activeRideRouteFor(status as string | null | undefined)).toBeNull();
    },
  );
});
