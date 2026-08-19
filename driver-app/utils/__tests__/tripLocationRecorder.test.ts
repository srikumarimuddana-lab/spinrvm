/* eslint-disable import/first */

jest.mock('expo-location', () => ({}));

// The recorder creates a module-level default outbox, even though these tests
// inject a fake one. Keep that import on the same lightweight seam used by the
// working background recorder tests.
jest.mock('../tripLocationOutbox', () => ({
  tripLocationOutbox: {},
}));

import { createTripLocationRecorder } from '../tripLocationRecorder';

// jest.setup.js already mocks AsyncStorage globally. Reach for that instance
// rather than re-mocking here: a local `const` would still be in its temporal
// dead zone when the hoisted import of tripLocationRecorder pulls the module in,
// leaving the recorder with an undefined AsyncStorage.
const mockAsyncStorage = jest.requireMock('@react-native-async-storage/async-storage') as {
  getItem: jest.Mock; setItem: jest.Mock; removeItem: jest.Mock;
};

const point = {
  ride_id: 'ride-1',
  recording_session_id: 'session-1',
  sequence_number: 4,
  captured_at: '2026-07-17T22:45:00.000Z',
  monotonic_ms: 10,
  lat: 50.45,
  lng: -104.62,
  accuracy: 8,
  speed: null,
  heading: null,
  altitude: null,
  source: 'completion' as const,
  mocked: false,
  is_completion_fix: true,
};

function createOutbox() {
  return {
    startSession: jest.fn(),
    enqueue: jest.fn(),
    listPendingSessions: jest.fn(),
    peek: jest.fn().mockResolvedValue([point]),
    acknowledge: jest.fn().mockResolvedValue(undefined),
    pendingCount: jest.fn(),
    closeSession: jest.fn(),
  };
}

describe('TripLocationRecorder completion acknowledgements', () => {
  test('applies the server acknowledgement to the matching durable session', async () => {
    const outbox = createOutbox();
    const recorder = createTripLocationRecorder({ outbox: outbox as any });

    const acknowledged = await recorder.applyAcknowledgement({
      recording_session_id: 'session-1',
      acked_through: 4,
      rejected: [{ sequence_number: 3, reason: 'invalid_coordinate' }],
    });

    expect(acknowledged).toBe(1);
    expect(outbox.acknowledge).toHaveBeenCalledWith('session-1', 4, [
      { sequence_number: 3, reason: 'invalid_coordinate' },
    ]);
  });

  test('does not delete points for a missing or non-contiguous acknowledgement', async () => {
    const outbox = createOutbox();
    const recorder = createTripLocationRecorder({ outbox: outbox as any });

    expect(await recorder.applyAcknowledgement({ recording_session_id: 'session-1', acked_through: null })).toBe(0);
    await expect(
      recorder.applyAcknowledgement({ recording_session_id: 'session-1', acked_through: 8 }),
    ).rejects.toThrow('exceeded the persisted point');
    expect(outbox.acknowledge).not.toHaveBeenCalled();
  });
});

describe('TripLocationRecorder purgeAll (sign-out)', () => {
  const activeRideKey = 'spinr_trip_location_active_ride';

  test('purges the outbox and forgets the active ride', async () => {
    const outbox = { ...createOutbox(), purgeAll: jest.fn().mockResolvedValue(undefined) };
    const recorder = createTripLocationRecorder({ outbox: outbox as any });

    outbox.startSession.mockResolvedValue({
      recording_session_id: 'session-1', ride_id: 'ride-1',
      opened_at: '2026-07-29T10:00:00.000Z', closed_at: null,
    });
    await recorder.startRide('ride-1');

    await recorder.purgeAll();

    expect(outbox.purgeAll).toHaveBeenCalledTimes(1);
    expect(mockAsyncStorage.removeItem).toHaveBeenCalledWith(activeRideKey);
    // The stale-attribution bug: a leftover active-ride pointer made the next
    // sign-in on this device record fresh fixes against the previous driver's ride.
    expect((await recorder.getRecorderHealth()).activeRideId).toBeNull();
  });

  test('purges the outbox even when the active-ride key cannot be cleared', async () => {
    const outbox = { ...createOutbox(), purgeAll: jest.fn().mockResolvedValue(undefined) };
    const recorder = createTripLocationRecorder({ outbox: outbox as any });
    mockAsyncStorage.removeItem.mockRejectedValueOnce(new Error('storage unavailable'));

    await expect(recorder.purgeAll()).resolves.toBeUndefined();
    // Removing the coordinates is the part that matters for PII retention.
    expect(outbox.purgeAll).toHaveBeenCalledTimes(1);
  });
});

describe('TripLocationRecorder flush backoff', () => {
  const session = {
    recording_session_id: 'session-1',
    ride_id: 'ride-1',
    opened_at: '2026-07-17T22:44:00.000Z',
    closed_at: null,
  };

  function createBackoffHarness() {
    let nowMs = 1_000_000;
    const outbox = createOutbox();
    outbox.listPendingSessions.mockResolvedValue([session]);
    // A real acknowledge drains the batch; without this the flush loop's
    // `while (true)` re-peeks the same point forever on a success path.
    let pending: (typeof point)[] = [point];
    outbox.peek.mockImplementation(async () => pending as any);
    outbox.acknowledge.mockImplementation(async () => {
      pending = [];
    });
    const recorder = createTripLocationRecorder({ outbox: outbox as any, now: () => nowMs });
    const failingTransport = jest.fn().mockRejectedValue(new Error('network down'));
    return {
      outbox,
      recorder,
      failingTransport,
      advance: (ms: number) => { nowMs += ms; },
      refill: () => { pending = [point]; },
    };
  }

  test('a failed flush opens a backoff window that force-flushes respect', async () => {
    const { recorder, failingTransport, advance } = createBackoffHarness();

    await expect(recorder.flushPending(failingTransport, { force: true })).rejects.toThrow('network down');
    expect(failingTransport).toHaveBeenCalledTimes(1);

    // Inside the window (base 5s, jitter <= +20% => <= 6s): even force skips.
    advance(1_000);
    const skipped = await recorder.flushPending(failingTransport, { force: true });
    expect(skipped.skipped).toBe(true);
    expect(failingTransport).toHaveBeenCalledTimes(1);

    // Past the max possible first window (6s): retried.
    advance(6_000);
    await expect(recorder.flushPending(failingTransport, { force: true })).rejects.toThrow('network down');
    expect(failingTransport).toHaveBeenCalledTimes(2);
  });

  test('backoff grows exponentially and clears on a successful transport', async () => {
    const { recorder, failingTransport, advance, refill } = createBackoffHarness();

    // Two consecutive failures => second window is 10s ±20% (8s..12s).
    await expect(recorder.flushPending(failingTransport, { force: true })).rejects.toThrow();
    advance(7_000);
    await expect(recorder.flushPending(failingTransport, { force: true })).rejects.toThrow();

    advance(7_000); // 7s < min second window (8s): still skipped
    expect((await recorder.flushPending(failingTransport, { force: true })).skipped).toBe(true);

    advance(6_000); // 13s total > max second window (12s): retried, now succeed
    const okTransport = jest.fn().mockResolvedValue({
      recording_session_id: 'session-1', acked_through: 4, rejected: [],
    });
    const ok = await recorder.flushPending(okTransport, { force: true });
    expect(ok.skipped).toBe(false);

    // Success cleared the backoff: an immediate next flush transports again.
    refill();
    const failAgain = jest.fn().mockRejectedValue(new Error('down again'));
    await expect(recorder.flushPending(failAgain, { force: true })).rejects.toThrow('down again');
    expect(failAgain).toHaveBeenCalledTimes(1);
  });

  test('the completion flush bypasses an active backoff window', async () => {
    const { recorder, failingTransport, advance } = createBackoffHarness();

    await expect(recorder.flushPending(failingTransport, { force: true })).rejects.toThrow();
    advance(1_000); // deep inside the window

    // flushPendingWithTimeout passes bypassBackoff — the GPS tail must reach
    // settlement immediately even mid-outage.
    const completionTransport = jest.fn().mockResolvedValue({
      recording_session_id: 'session-1', acked_through: 4, rejected: [],
    });
    const bounded = await recorder.flushPendingWithTimeout(completionTransport, 5_000);
    expect(bounded.timedOut).toBe(false);
    expect(completionTransport).toHaveBeenCalledTimes(1);
  });

  test('backoff keeps the health snapshot degraded for the whole window', async () => {
    const { recorder, failingTransport, advance } = createBackoffHarness();
    mockAsyncStorage.getItem.mockResolvedValue(null);

    await expect(recorder.flushPending(failingTransport, { force: true })).rejects.toThrow();
    // Past the 30s recent-failure horizon but inside a later, longer window:
    // fail 3 more times to push the window past 40s (4th window 40s ±20%).
    for (let i = 0; i < 3; i += 1) {
      advance(50_000);
      await expect(recorder.flushPending(failingTransport, { force: true })).rejects.toThrow();
    }
    advance(31_000); // recent-failure clause expired; window (>=32s) still live
    const health = await recorder.getRecorderHealth('ride-1');
    expect(health.degraded).toBe(true);
    expect(health.degradationReason).toBe('upload_failure');
  });
});

describe('TripLocationRecorder drop telemetry', () => {
  test('counts no-ride drops and reports once at closeRide, counts only', async () => {
    const outbox = createOutbox();
    outbox.closeSession.mockResolvedValue(undefined);
    const reportNonFatal = jest.fn();
    const recorder = createTripLocationRecorder({ outbox: outbox as any, reportNonFatal });
    mockAsyncStorage.getItem.mockResolvedValue(null); // no active ride key

    const loc = { timestamp: 1_000, coords: { latitude: 52.1, longitude: -106.6, accuracy: 5, speed: 1, heading: 0, altitude: null } } as any;
    expect(await recorder.recordNativeFix(loc, 'background')).toBeNull();
    expect(await recorder.recordNativeFix(loc, 'background')).toBeNull();

    const health = await recorder.getRecorderHealth('ride-x');
    expect(health.drops).toEqual({ droppedNoRide: 2, enqueueFailures: 0 });

    await recorder.closeRide('ride-x');
    expect(reportNonFatal).toHaveBeenCalledTimes(1);
    const [error, context] = reportNonFatal.mock.calls[0];
    expect(String(error.message)).toContain('gps capture drops');
    expect(context.dropped_no_ride).toBe(2);
    // PII rule: counts only — the report must never carry coordinates.
    expect(JSON.stringify(context)).not.toContain('52.1');

    // Counters reset after the report: a clean next ride reports nothing.
    await recorder.closeRide('ride-x');
    expect(reportNonFatal).toHaveBeenCalledTimes(1);
  });

  test('counts enqueue failures and still rethrows', async () => {
    const outbox = createOutbox();
    outbox.enqueue.mockRejectedValue(new Error('disk full'));
    const reportNonFatal = jest.fn();
    const recorder = createTripLocationRecorder({ outbox: outbox as any, reportNonFatal });

    const loc = { timestamp: 1_000, coords: { latitude: 52.1, longitude: -106.6, accuracy: 5, speed: 1, heading: 0, altitude: null } } as any;
    await expect(recorder.recordNativeFix(loc, 'foreground', 'ride-1')).rejects.toThrow('disk full');

    const health = await recorder.getRecorderHealth('ride-1');
    expect(health.drops.enqueueFailures).toBe(1);
  });
});

describe('TripLocationRecorder Period-1 idle sessions', () => {
  const loc = (lat = 52.1, ts = 1_000) => ({
    timestamp: ts,
    coords: { latitude: lat, longitude: -106.6, accuracy: 5, speed: 8, heading: 0, altitude: null },
  }) as any;

  function idleHarness() {
    let nowMs = 1_000_000;
    const outbox = createOutbox();
    outbox.startSession.mockResolvedValue({
      recording_session_id: 'idle-session', ride_id: '@idle',
      opened_at: '2026-08-18T12:00:00.000Z', closed_at: null,
    });
    outbox.enqueue.mockImplementation(async (fix) => ({
      ...fix, recording_session_id: 'idle-session', sequence_number: 0,
    }));
    const recorder = createTripLocationRecorder({ outbox: outbox as any, now: () => nowMs });
    mockAsyncStorage.getItem.mockResolvedValue(null); // no active trip
    return { outbox, recorder, advance: (ms: number) => { nowMs += ms; } };
  }

  test('records throttled idle fixes into the @idle session when enabled', async () => {
    const { outbox, recorder, advance } = idleHarness();
    recorder.setIdleRecordingEnabled(true);
    await recorder.startIdleSession();
    expect(outbox.startSession).toHaveBeenCalledWith('@idle');

    const first = await recorder.recordNativeFix(loc(), 'background');
    expect(first?.ride_id).toBe('@idle');

    // Inside the 30s background throttle and <100m moved: dropped (counted).
    advance(5_000);
    expect(await recorder.recordNativeFix(loc(52.1001), 'background')).toBeNull();

    // Past the throttle: recorded.
    advance(30_000);
    expect((await recorder.recordNativeFix(loc(52.101), 'background'))?.ride_id).toBe('@idle');

    // Large movement bypasses the interval throttle.
    advance(1_000);
    expect((await recorder.recordNativeFix(loc(52.15), 'background'))?.ride_id).toBe('@idle');
  });

  test('idle fixes are dropped when the flag is off or no session is open', async () => {
    const { outbox, recorder } = idleHarness();
    expect(await recorder.recordNativeFix(loc(), 'background')).toBeNull();
    recorder.setIdleRecordingEnabled(true); // enabled but session not opened
    expect(await recorder.recordNativeFix(loc(), 'background')).toBeNull();
    expect(outbox.enqueue).not.toHaveBeenCalled();
  });

  test('flush translates the @idle session into the ride-less session_kind shape', async () => {
    const { outbox, recorder } = idleHarness();
    const idlePoint = { ...point, ride_id: '@idle', recording_session_id: 'idle-session', sequence_number: 0 };
    outbox.listPendingSessions.mockResolvedValue([
      { recording_session_id: 'idle-session', ride_id: '@idle', opened_at: 'x', closed_at: null },
    ]);
    let pending = [idlePoint];
    outbox.peek.mockImplementation(async () => pending as any);
    outbox.acknowledge.mockImplementation(async () => { pending = []; });
    const transport = jest.fn().mockResolvedValue({
      recording_session_id: 'idle-session', acked_through: 0, rejected: [],
    });

    await recorder.flushPending(transport, { force: true });

    const request = transport.mock.calls[0][0];
    expect(request.session_kind).toBe('online_idle');
    expect(request.recording_session_id).toBe('idle-session');
    expect(request).not.toHaveProperty('ride_id');
  });

  test('startRide closes the idle session (the trip owns fixes now)', async () => {
    const { outbox, recorder } = idleHarness();
    recorder.setIdleRecordingEnabled(true);
    await recorder.startIdleSession();
    outbox.startSession.mockResolvedValue({
      recording_session_id: 'trip-session', ride_id: 'ride-1', opened_at: 'x', closed_at: null,
    });

    await recorder.startRide('ride-1');

    expect(outbox.closeSession).toHaveBeenCalledWith('@idle');
    // With a trip active, a no-throttle idle fix would now resolve to the ride.
    mockAsyncStorage.getItem.mockResolvedValue('ride-1');
    const recorded = await recorder.recordNativeFix(loc(), 'background');
    expect(recorded?.ride_id).toBe('ride-1');
  });
});
