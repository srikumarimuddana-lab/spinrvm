import { smoothFix, isImplausibleJump, MAX_PLAUSIBLE_SPEED_MPS } from '@shared/utils/gpsSmoothing';
import { distanceMeters } from '@shared/utils/vehicleTracking';

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

  it('stays stable under repeated GPS jitter while parked (2026-09-14 live-testing report)', () => {
    // A phone sitting still (on a table, or a genuinely parked car) still
    // reports a fresh fix every ~2.5s (driver-app's MARKER_HEARTBEAT_MS) with
    // a few metres of multipath/weak-sky-view noise each time — worse
    // indoors/near buildings. Before the speed-adaptive process noise, every
    // one of these fixes was damped using the SAME looseness tuned for a
    // moving car, so consecutive noisy fixes could drag the smoothed
    // estimate several metres in essentially random directions between
    // ticks — markerPlayback.ts's MIN_SEGMENT_MOVE_M (2m) then read each
    // drag as a real directional segment and gave it a bearing, which is
    // what "the icon rotates back and forth while stationary" looks like.
    const center = { latitude: 52.1, longitude: -106.6 };
    // Deterministic ~2-4m jitter pattern in varying directions (not
    // Math.random, so the test is reproducible) — degrees chosen so 0.00003
    // lat ≈ 3.3m and 0.00003 lng at this latitude ≈ 2.0m.
    const jitters = [
      { dLat: 0.00003, dLng: 0 },
      { dLat: -0.00002, dLng: 0.00002 },
      { dLat: 0.00001, dLng: -0.00003 },
      { dLat: -0.00003, dLng: 0.00001 },
      { dLat: 0.00002, dLng: 0.00002 },
      { dLat: -0.00001, dLng: -0.00002 },
      { dLat: 0.00003, dLng: -0.00001 },
      { dLat: -0.00002, dLng: 0.00003 },
    ];

    // The metric that matters is the PER-TICK segment distance, not
    // cumulative drift from the center: markerPlayback.ts's
    // MIN_SEGMENT_MOVE_M (2m) is checked per tick — a single jittery
    // step above it gets a real (spurious) bearing even if later steps
    // happen to cancel it out and the running position ends up near center.
    let state = smoothFix(null, { ...center, timestampMs: 0 });
    let maxSegM = 0;
    for (let i = 0; i < jitters.length; i++) {
      const t = (i + 1) * 2_500; // MARKER_HEARTBEAT_MS cadence
      const next = smoothFix(state, {
        latitude: center.latitude + jitters[i].dLat,
        longitude: center.longitude + jitters[i].dLng,
        timestampMs: t,
      });
      maxSegM = Math.max(maxSegM, distanceMeters(state.latitude, state.longitude, next.latitude, next.longitude));
      state = next;
    }
    // Threshold (2.0 m/s) was picked by direct measurement, not a guess —
    // see the constant's own doc comment in gpsSmoothing.ts for the
    // jitter-vs-crawl tradeoff that produced it. This 8-fix adversarial
    // sequence's worst tick drops from 3.44m (fixed 6 m/s process noise,
    // computed by hand against this same formula) to under 2m here, which
    // is what keeps markerPlayback.ts from assigning it a bearing at all.
    expect(maxSegM).toBeLessThan(2);
  });

  it('still tracks a slow real crawl rather than mistaking it for parked', () => {
    // Guards the opposite failure mode: a driver creeping forward from a
    // stop just above STATIONARY_SPEED_THRESHOLD_MPS (2.0) must not be
    // damped as if parked. One fix every 2s for 20s at 2.5 m/s.
    let state = null as ReturnType<typeof smoothFix> | null;
    let lat = 52.1;
    const speedMps = 2.5;
    const perTickLatDelta = (speedMps * 2) / 111_320; // ~2s of travel, degrees latitude
    for (let t = 0; t <= 20_000; t += 2_000) {
      state = smoothFix(state, { latitude: lat, longitude: -106.6, timestampMs: t });
      lat += perTickLatDelta;
    }
    const trueFinalLat = 52.1 + perTickLatDelta * 10;
    expect(state).not.toBeNull();
    const laggedByM = distanceMeters(state!.latitude, -106.6, trueFinalLat, -106.6);
    expect(laggedByM).toBeLessThan(8); // true displacement is ~50m; not stuck near the start
  });

  it('fully trusts a fresh fix after a long gap even at moderate average speed (spinr-edge-case-reviewer finding, 2026-09-14)', () => {
    // A genuine drive-away spread across a backgrounding/tunnel gap can
    // average out to a deceptively low implied speed even though the
    // vehicle is now moving and the filter should trust the fresh fix MOST:
    // 30m over a 20s gap implies 1.5 m/s, under STATIONARY_SPEED_THRESHOLD_MPS
    // (2.0) if the gap ceiling didn't exist. MAX_STATIONARY_GAP_SEC (10s)
    // means this 20s gap must fall back to full trust (the caller's own
    // processNoiseMps, unmodified) rather than being extra-damped.
    const seeded = smoothFix(null, { latitude: 52.1, longitude: -106.6, timestampMs: 0 });
    // ~30m north over 20s.
    const after = smoothFix(seeded, { latitude: 52.1 + 0.00027, longitude: -106.6, timestampMs: 20_000 });
    const movedM = distanceMeters(seeded.latitude, seeded.longitude, after.latitude, after.longitude);
    // With full trust (gain close to 1 given the large predictedVariance a
    // 20s*36 process-noise term produces), the estimate should land close to
    // the new fix, not be held back near the stale pre-gap position.
    expect(movedM).toBeGreaterThan(20); // true segment is ~30m
  });

  it('damps a low-accuracy (weak-signal) fix harder than the same jitter reported as high-accuracy', () => {
    // Same ~30m jitter, same elapsed time — only the reported accuracy
    // differs. A parked vehicle near buildings/underground parking commonly
    // reports a large accuracyM on a drifted fix (this is the exact
    // "ghost movement while parked" scenario: CarMarker.tsx spreads its raw
    // fix — including accuracyM, once a producer supplies one — straight
    // into this function). The Kalman gain must fall as measurement
    // variance rises, so the same drift moves the estimate less when the
    // fix says it's unreliable.
    const seededGood = smoothFix(null, { latitude: 52.1, longitude: -106.6, timestampMs: 0 });
    const seededBad = smoothFix(null, { latitude: 52.1, longitude: -106.6, timestampMs: 0 });

    const goodAccuracy = smoothFix(
      seededGood,
      { latitude: 52.1003, longitude: -106.6, timestampMs: 1_000, accuracyM: 5 },
    );
    const poorAccuracy = smoothFix(
      seededBad,
      { latitude: 52.1003, longitude: -106.6, timestampMs: 1_000, accuracyM: 40 },
    );

    const goodShift = goodAccuracy.latitude - seededGood.latitude;
    const poorShift = poorAccuracy.latitude - seededBad.latitude;
    expect(poorShift).toBeGreaterThan(0); // still moves toward the fix, just less
    expect(poorShift).toBeLessThan(goodShift);
  });

  it('falls back to the documented default accuracy when the fix omits one', () => {
    // A producer (e.g. rider-app's WS-relayed fix) that doesn't have a real
    // accuracy value must not crash or silently disable damping — it should
    // behave exactly as this module did before accuracyM plumbing existed.
    const seededNoAccuracy = smoothFix(null, { latitude: 52.1, longitude: -106.6, timestampMs: 0 });
    const seededOmitted = smoothFix(null, { latitude: 52.1, longitude: -106.6, timestampMs: 0 });
    const withExplicitDefault = smoothFix(
      seededNoAccuracy,
      { latitude: 52.1003, longitude: -106.6, timestampMs: 1_000, accuracyM: 8 }, // DEFAULT_ACCURACY_M
    );
    const withOmittedField = smoothFix(
      seededOmitted,
      { latitude: 52.1003, longitude: -106.6, timestampMs: 1_000 }, // no accuracyM at all
    );
    expect(withOmittedField.latitude).toBeCloseTo(withExplicitDefault.latitude, 12);
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
