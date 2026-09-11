import {
  MIN_DISPLAYED_SPEED_MPS,
  MAX_SPEED_FIX_AGE_MS,
  displaySpeedKmh,
  shouldDisplayFix,
  zoomTierForSpeed,
} from '../../utils/locationDisplayGate';

describe('displaySpeedKmh', () => {
  const NOW = 1_700_000_000_000;

  it('converts a fresh, above-floor speed to rounded km/h', () => {
    // 15.8 m/s ~= 56.9 km/h
    expect(displaySpeedKmh(15.8, NOW - 1000, NOW)).toBe(57);
  });

  it('clamps a fresh but noisy near-zero speed to 0', () => {
    // 2.2 m/s (~8 km/h) is below MIN_DISPLAYED_SPEED_MPS
    expect(displaySpeedKmh(2.2, NOW - 1000, NOW)).toBe(0);
  });

  it('clamps a stale fix to 0 regardless of its own speed value', () => {
    // The live-reported bug: 57 km/h held on screen after the vehicle
    // actually stopped, because no fresh fix arrived to overwrite it.
    const staleTimestamp = NOW - (MAX_SPEED_FIX_AGE_MS + 1);
    expect(displaySpeedKmh(15.8, staleTimestamp, NOW)).toBe(0);
  });

  it('does not clamp a fix right at the staleness boundary', () => {
    const boundaryTimestamp = NOW - MAX_SPEED_FIX_AGE_MS;
    expect(displaySpeedKmh(15.8, boundaryTimestamp, NOW)).toBe(57);
  });

  it('treats a missing timestamp as fresh (never clamps for staleness)', () => {
    expect(displaySpeedKmh(15.8, null, NOW)).toBe(57);
    expect(displaySpeedKmh(15.8, undefined, NOW)).toBe(57);
  });

  it('treats a null/undefined speed as 0', () => {
    expect(displaySpeedKmh(null, NOW, NOW)).toBe(0);
    expect(displaySpeedKmh(undefined, NOW, NOW)).toBe(0);
  });

  it('handles a negative platform placeholder speed as 0, not NaN', () => {
    expect(displaySpeedKmh(-1, NOW, NOW)).toBe(0);
  });
});

// Pre-existing exports — no behavior change from this file's edits, just
// confirming nothing broke while adding displaySpeedKmh alongside them.
describe('pre-existing exports unaffected', () => {
  it('MIN_DISPLAYED_SPEED_MPS is unchanged', () => {
    expect(MIN_DISPLAYED_SPEED_MPS).toBe(3);
  });

  it('shouldDisplayFix and zoomTierForSpeed still work', () => {
    expect(shouldDisplayFix(10, 0)).toBe(true);
    expect(zoomTierForSpeed(0, null)).toBe(0);
  });
});
