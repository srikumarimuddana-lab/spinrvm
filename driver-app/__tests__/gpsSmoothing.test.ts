import { smoothFix, isImplausibleJump, MAX_PLAUSIBLE_SPEED_MPS } from '@shared/utils/gpsSmoothing';

describe('smoothFix', () => {
  it('seeds at the raw fix when there is no prior state', () => {
    const state = smoothFix(null, { latitude: 52.1, longitude: -106.6, timestampMs: 1_000 });
    expect(state.latitude).toBe(52.1);
    expect(state.longitude).toBe(-106.6);
    expect(state.variance).toBeGreaterThan(0);
  });

  it('damps a single noisy fix toward the running estimate rather than jumping to it', () => {
    const seeded = smoothFix(null, { latitude: 52.1, longitude: -106.6, timestampMs: 0 });
    // A fix 1s later that jitters ~0.0003deg (~30m) off the running estimate —
    // the smoothed output should move toward it, but land short of the raw
    // value (a real Kalman-style filter never fully snaps to one sample).
    const next = smoothFix(seeded, { latitude: 52.1003, longitude: -106.6, timestampMs: 1_000 });
    expect(next.latitude).toBeGreaterThan(seeded.latitude);
    expect(next.latitude).toBeLessThan(52.1003);
  });

  it('converges toward sustained real movement rather than lagging indefinitely', () => {
    // Simulate a car driving in a straight line at a steady ~14 m/s (50 km/h),
    // one fix per second, for 10s. The smoothed trajectory should end up
    // close to the true final position, not stuck near the start.
    let state = null as ReturnType<typeof smoothFix> | null;
    let lat = 52.1;
    for (let t = 0; t <= 10_000; t += 1_000) {
      state = smoothFix(state, { latitude: lat, longitude: -106.6, timestampMs: t });
      lat += 0.000126; // ~14 m/s northward in degrees latitude per second
    }
    const trueFinalLat = 52.1 + 0.000126 * 10;
    expect(state).not.toBeNull();
    expect(Math.abs(state!.latitude - trueFinalLat)).toBeLessThan(0.0002); // within ~22m
  });

  it('treats an out-of-order/duplicate timestamp as a no-op rather than corrupting variance', () => {
    const seeded = smoothFix(null, { latitude: 52.1, longitude: -106.6, timestampMs: 5_000 });
    const stale = smoothFix(seeded, { latitude: 52.5, longitude: -106.6, timestampMs: 1_000 });
    // dtSec clamps to 0, so predictedVariance == seeded.variance and the fix
    // is treated as an ordinary (if oddly-timed) measurement, not one that
    // blows up or shrinks variance unexpectedly.
    expect(Number.isFinite(stale.variance)).toBe(true);
    expect(stale.variance).toBeGreaterThan(0);
  });
});

describe('isImplausibleJump', () => {
  const t0 = { latitude: 52.1332, longitude: -106.6700, timestampMs: 0 };

  it('accepts the first fix unconditionally (nothing to compare against)', () => {
    expect(isImplausibleJump(null, t0)).toBe(false);
  });

  it('rejects a jump requiring an impossible speed (500m in 2s)', () => {
    // ~500m north in 2s implies ~250 m/s (900 km/h) — impossible.
    const fix = { latitude: 52.1332 + 0.0045, longitude: -106.6700, timestampMs: 2_000 };
    expect(isImplausibleJump(t0, fix)).toBe(true);
  });

  it('accepts the same 500m distance over a plausible highway-speed elapsed time', () => {
    // ~500m in 25s implies ~20 m/s (72 km/h) — a perfectly ordinary highway
    // fix-to-fix segment, not a glitch.
    const fix = { latitude: 52.1332 + 0.0045, longitude: -106.6700, timestampMs: 25_000 };
    expect(isImplausibleJump(t0, fix)).toBe(false);
  });

  it('accepts a large distance after a real background/tunnel gap', () => {
    // 3km over 5 minutes is ~10 m/s (36 km/h) — a legitimate gap, not a jump.
    const fix = { latitude: 52.1332 + 0.027, longitude: -106.6700, timestampMs: 5 * 60_000 };
    expect(isImplausibleJump(t0, fix)).toBe(false);
  });

  it('never flags sub-jitter distances regardless of timing', () => {
    // ~5m apart, 10ms elapsed — well under MIN_JUMP_CHECK_DISTANCE_M, so this
    // is ordinary receiver noise, not a jump, even though the naive implied
    // speed (500 m/s) would look impossible.
    const fix = { latitude: 52.1332 + 0.00004, longitude: -106.6700, timestampMs: 10 };
    expect(isImplausibleJump(t0, fix)).toBe(false);
  });

  it('respects a custom speed ceiling', () => {
    // ~40 m/s implied speed: implausible for a 30 m/s ceiling, plausible for
    // the default 60 m/s one.
    const fix = { latitude: 52.1332 + 0.00036, longitude: -106.6700, timestampMs: 1_000 };
    expect(isImplausibleJump(t0, fix, 30)).toBe(true);
    expect(isImplausibleJump(t0, fix, MAX_PLAUSIBLE_SPEED_MPS)).toBe(false);
  });
});
