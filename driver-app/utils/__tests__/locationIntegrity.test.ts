/**
 * Per-producer integrity checker contract.
 *
 * The teleport test compares consecutive fixes FROM THE SAME PRODUCER. The
 * pre-factory module-global state let the Android Auto watcher (2 s cadence)
 * interleave with the background trip task (4 s) and falsely reject
 * legitimate fixes as teleport_detected — dropped before the outbox, i.e.
 * silent route loss (SPR-PE7TTB class). These tests pin the isolation and the
 * registry-wide reset that go-offline / sign-out rely on.
 */
import {
  checkLocationIntegrity,
  createLocationIntegrityChecker,
  resetLocationIntegrity,
} from '../locationIntegrity';

jest.mock('react-native', () => ({ Platform: { OS: 'android' } }));

const fix = (lat: number, lng: number, timestamp: number, extra: Record<string, unknown> = {}) =>
  ({
    timestamp,
    mocked: false,
    coords: { latitude: lat, longitude: lng, speed: 0, accuracy: 5, altitude: 0, heading: 0 },
    ...extra,
  }) as any;

describe('createLocationIntegrityChecker', () => {
  it('rejects mocked, impossible-speed, and teleport fixes', () => {
    const c = createLocationIntegrityChecker();
    expect(c.check(fix(52.1, -106.6, 1_000, { mocked: true })).reason).toBe('mock_location_detected');
    const speeding = fix(52.1, -106.6, 2_000);
    speeding.coords.speed = 120;
    expect(c.check(speeding).reason).toBe('impossible_speed');
    expect(c.check(fix(52.1, -106.6, 3_000)).trusted).toBe(true);
    // >5km jump in <5s
    expect(c.check(fix(52.9, -106.6, 4_000)).reason).toBe('teleport_detected');
  });

  it('keeps per-producer state isolated — cross-producer interleaving cannot trip teleport', () => {
    const producerA = createLocationIntegrityChecker();
    const producerB = createLocationIntegrityChecker();

    // Producer A reports from downtown; producer B reports from 8 km away 1s
    // later (e.g. deferred background batch vs live AA watcher timestamps).
    expect(producerA.check(fix(52.10, -106.60, 10_000)).trusted).toBe(true);
    // Under shared state this next call would compare against producer A's fix
    // (8km in 1s → teleport). With isolation it is producer B's FIRST fix.
    expect(producerB.check(fix(52.17, -106.72, 11_000)).trusted).toBe(true);
    // And producer A continuing its own smooth track stays trusted.
    expect(producerA.check(fix(52.101, -106.601, 12_000)).trusted).toBe(true);
  });

  it('resetLocationIntegrity clears EVERY registered checker', () => {
    const c = createLocationIntegrityChecker();
    expect(c.check(fix(52.1, -106.6, 20_000)).trusted).toBe(true);
    resetLocationIntegrity();
    // After the sweep, a huge jump 1s later is a FIRST fix again — no teleport.
    expect(c.check(fix(53.9, -104.0, 21_000)).trusted).toBe(true);
  });

  it('back-compat module-level checkLocationIntegrity still works and resets with the sweep', () => {
    expect(checkLocationIntegrity(fix(52.1, -106.6, 30_000)).trusted).toBe(true);
    expect(checkLocationIntegrity(fix(52.9, -106.6, 31_000)).reason).toBe('teleport_detected');
    resetLocationIntegrity();
    expect(checkLocationIntegrity(fix(52.1, -106.6, 32_000)).trusted).toBe(true);
  });
});
