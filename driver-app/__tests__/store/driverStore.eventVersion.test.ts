import { useDriverStore } from '../../store/driverStore';

// V4 (issue #11): the driver must drop stale / out-of-order ride_status_changed
// events by comparing the server-authoritative monotonic `version`
// (rides.version, bumped by the migration-225 trigger and stamped into the
// payload by broadcast_ride_status). shouldApplyRideEvent is the pure decision
// used by the WS handler in useDriverDashboard.

describe('driverStore.shouldApplyRideEvent (monotonic ride event ordering)', () => {
  beforeEach(() => {
    // Reset the version tracker between tests.
    useDriverStore.setState({ _lastEventRideId: null, _lastEventVersion: -1 });
  });

  it('applies the first versioned event for a ride and records its version', () => {
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 5)).toBe(true);
  });

  it('drops an event whose version is lower than the highest applied (stale)', () => {
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 5)).toBe(true);
    // A delayed driver_arrived arriving after in_progress, etc.
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 3)).toBe(false);
  });

  it('drops a duplicate (same version is not strictly greater)', () => {
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 5)).toBe(true);
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 5)).toBe(false);
  });

  it('applies a strictly newer version and advances the baseline', () => {
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 5)).toBe(true);
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 7)).toBe(true);
    // 6 now stale relative to 7.
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 6)).toBe(false);
  });

  it('resets the baseline for a different ride (versions are per-ride)', () => {
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 9)).toBe(true);
    // A brand-new ride starts its own version sequence — a low version here
    // must NOT be dropped as stale against the previous ride's high version.
    expect(useDriverStore.getState().shouldApplyRideEvent('r2', 1)).toBe(true);
    expect(useDriverStore.getState().shouldApplyRideEvent('r2', 2)).toBe(true);
  });

  it('always applies an event with no version (un-migrated backend / compat)', () => {
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', undefined)).toBe(true);
    // A missing version must not disturb the tracked baseline either.
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 4)).toBe(true);
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', 4)).toBe(false);
    // Still applies an absent-version event even after a baseline is set.
    expect(useDriverStore.getState().shouldApplyRideEvent('r1', undefined)).toBe(true);
  });
});
