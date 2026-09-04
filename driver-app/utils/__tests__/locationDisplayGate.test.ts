import {
  ACCURACY_OVERRIDE_MS,
  FOLLOW_ZOOM_TIERS,
  MAX_DISPLAY_ACCURACY_M,
  MIN_DISPLAYED_SPEED_MPS,
  shouldDisplayFix,
  zoomTierForSpeed,
} from '../locationDisplayGate';

describe('shouldDisplayFix', () => {
  it('accepts a normal GPS fix', () => {
    expect(shouldDisplayFix(8, 0)).toBe(true);
    expect(shouldDisplayFix(MAX_DISPLAY_ACCURACY_M, 0)).toBe(true);
  });

  it('rejects a cell/wifi-grade fix while a recent good fix exists', () => {
    expect(shouldDisplayFix(120, 5_000)).toBe(false);
    expect(shouldDisplayFix(MAX_DISPLAY_ACCURACY_M + 1, 0)).toBe(false);
  });

  it('overrides after the stale window so the marker never freezes', () => {
    expect(shouldDisplayFix(120, ACCURACY_OVERRIDE_MS)).toBe(true);
    expect(shouldDisplayFix(120, ACCURACY_OVERRIDE_MS - 1)).toBe(false);
  });

  it('trusts fixes with no reported accuracy', () => {
    expect(shouldDisplayFix(null, 0)).toBe(true);
    expect(shouldDisplayFix(undefined, 0)).toBe(true);
    expect(shouldDisplayFix(-1, 0)).toBe(true);
  });
});

describe('MIN_DISPLAYED_SPEED_MPS', () => {
  it('clears the live-reported stationary GPS-noise reading (8 km/h) with margin', () => {
    const reportedStationaryKmh = 8;
    const reportedStationaryMps = reportedStationaryKmh / 3.6;
    expect(MIN_DISPLAYED_SPEED_MPS).toBeGreaterThan(reportedStationaryMps);
  });

  it('stays well below real driving speed so movement is still shown promptly', () => {
    const slowResidentialKmh = 20;
    expect(MIN_DISPLAYED_SPEED_MPS).toBeLessThan(slowResidentialKmh / 3.6);
  });
});

describe('zoomTierForSpeed', () => {
  it('maps stopped → closest tier, city → middle, fast → farthest', () => {
    expect(zoomTierForSpeed(0, null)).toBe(0);
    expect(zoomTierForSpeed(5, null)).toBe(1);
    expect(zoomTierForSpeed(20, null)).toBe(2);
  });

  it('holds the tier inside the hysteresis band', () => {
    // Boundary tier0→tier1 is 2 m/s: 2.4 is within +0.75 margin — hold tier 0.
    expect(zoomTierForSpeed(2.4, 0)).toBe(0);
    // 3.0 clears the margin — move to tier 1.
    expect(zoomTierForSpeed(3.0, 0)).toBe(1);
    // Slowing: at tier 1, 1.6 is not ≤ 2 − 0.75 — hold tier 1.
    expect(zoomTierForSpeed(1.6, 1)).toBe(1);
    // 1.0 clears the down-margin — move to tier 0.
    expect(zoomTierForSpeed(1.0, 1)).toBe(0);
  });

  it('keeps the previous tier for unknown speed', () => {
    expect(zoomTierForSpeed(null, 2)).toBe(2);
    expect(zoomTierForSpeed(-1, 1)).toBe(1);
    expect(zoomTierForSpeed(null, null)).toBe(0);
  });

  it('tier table is ordered and ends open-ended', () => {
    expect(FOLLOW_ZOOM_TIERS[FOLLOW_ZOOM_TIERS.length - 1].maxSpeedMps).toBe(Infinity);
    for (let i = 1; i < FOLLOW_ZOOM_TIERS.length; i++) {
      expect(FOLLOW_ZOOM_TIERS[i].maxSpeedMps).toBeGreaterThan(FOLLOW_ZOOM_TIERS[i - 1].maxSpeedMps);
      expect(FOLLOW_ZOOM_TIERS[i].zoom).toBeLessThan(FOLLOW_ZOOM_TIERS[i - 1].zoom);
    }
  });
});
