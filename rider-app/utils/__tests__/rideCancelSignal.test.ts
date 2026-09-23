import { shouldLeaveScreenForRideCancelled } from '../rideCancelSignal';

describe('shouldLeaveScreenForRideCancelled', () => {
  it('ignores a cancel for the previous ride while a new ride is current', () => {
    expect(shouldLeaveScreenForRideCancelled('ride-1', 'ride-2', 'ride-1')).toBe(false);
  });

  it('leaves the screen when the cancel is for the ride on screen', () => {
    expect(shouldLeaveScreenForRideCancelled('ride-2', 'ride-2', 'ride-1')).toBe(true);
  });

  it('does not navigate home when there is no current ride', () => {
    expect(shouldLeaveScreenForRideCancelled('ride-1', undefined, 'ride-1')).toBe(false);
    expect(shouldLeaveScreenForRideCancelled('ride-1', undefined, null)).toBe(false);
  });

  it('still leaves when a cancel arrives with no ride id and a ride is on screen', () => {
    expect(shouldLeaveScreenForRideCancelled(undefined, 'ride-2', null)).toBe(true);
  });
});
