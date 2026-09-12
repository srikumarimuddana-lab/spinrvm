import {
  MIN_DISPLAYED_SPEED_MPS,
  MAX_SPEED_FIX_AGE_MS,
  FOLLOW_ZOOM_TIERS,
  displaySpeedKmh,
  effectiveSpeedMps,
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

describe('effectiveSpeedMps', () => {
  const NOW = 1_700_000_000_000;

  it('clamps a fresh but noisy near-zero speed to 0, in m/s', () => {
    expect(effectiveSpeedMps(2.2, NOW - 1000, NOW)).toBe(0);
  });

  it('clamps a stale fix to 0 regardless of its own speed value', () => {
    const staleTimestamp = NOW - (MAX_SPEED_FIX_AGE_MS + 1);
    expect(effectiveSpeedMps(15.8, staleTimestamp, NOW)).toBe(0);
  });

  it('passes through a fresh, above-floor speed unrounded', () => {
    expect(effectiveSpeedMps(15.8, NOW - 1000, NOW)).toBe(15.8);
  });

  it('feeding it into zoomTierForSpeed keeps a parked vehicle in the stopped tier', () => {
    // Live-reported: a parked vehicle's Doppler-derived speed has been seen
    // reading ~2.2 m/s from GPS noise alone — below FOLLOW_ZOOM_TIERS[0]'s
    // 2 m/s ceiling's own hysteresis margin, so the raw value used to be able
    // to cross into tier 1 (city) and back purely from noise, flickering the
    // follow-camera's zoom while genuinely stationary. Clamping through
    // effectiveSpeedMps first removes the noise before it ever reaches the
    // tier selector.
    const noisyParkedSpeed = 2.2;
    const tier = zoomTierForSpeed(
      effectiveSpeedMps(noisyParkedSpeed, NOW - 1000, NOW),
      0, // previously in the stopped tier
    );
    expect(tier).toBe(0);
    expect(FOLLOW_ZOOM_TIERS[tier].zoom).toBe(17.5);
  });

  it('a stale reading cannot hold the camera zoomed to a moving tier', () => {
    // Mirrors the speed-chip's own "stuck at 57km/h" bug: without staleness
    // clamping, a frozen `location.coords.speed` from before the vehicle
    // stopped would keep the follow-camera zoomed out at the highway tier
    // indefinitely.
    const staleTimestamp = NOW - (MAX_SPEED_FIX_AGE_MS + 1);
    const tier = zoomTierForSpeed(
      effectiveSpeedMps(20, staleTimestamp, NOW),
      2, // previously in the highway tier
    );
    expect(tier).toBe(0);
  });
});
