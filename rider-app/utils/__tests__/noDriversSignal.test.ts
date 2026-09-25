import { isNoDriversCancellation } from '../noDriversSignal';

describe('isNoDriversCancellation', () => {
  it('matches the backend cancellation_type', () => {
    expect(isNoDriversCancellation({ cancellation_type: 'no_drivers_found' })).toBe(true);
  });

  it("matches the sweeper's older reason-only WS payload", () => {
    expect(isNoDriversCancellation({ reason: 'no_drivers_found' })).toBe(true);
  });

  it('ignores rider, driver and other cancels', () => {
    expect(isNoDriversCancellation({ cancellation_type: 'rider_cancel' })).toBe(false);
    expect(isNoDriversCancellation({ reason: 'rider_cancelled' })).toBe(false);
    expect(isNoDriversCancellation({ reason: 'driver_cancelled' })).toBe(false);
    // ride_search_timeout's ride_cancelled `reason` is display text, not a code.
    expect(isNoDriversCancellation({
      reason: 'No nearby drivers available. Your ride has been automatically cancelled.',
    })).toBe(false);
  });

  it('handles missing input', () => {
    expect(isNoDriversCancellation(null)).toBe(false);
    expect(isNoDriversCancellation(undefined)).toBe(false);
    expect(isNoDriversCancellation({})).toBe(false);
  });
});
