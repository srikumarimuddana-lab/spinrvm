/* eslint-disable import/first */

jest.mock('expo-location', () => ({}));

jest.mock('../tripLocationOutbox', () => ({
  tripLocationOutbox: {},
}));

import { createTripLocationRecorder } from '../tripLocationRecorder';

const point = (sequenceNumber: number) => ({
  ride_id: 'ride-1',
  recording_session_id: 'session-1',
  sequence_number: sequenceNumber,
  captured_at: '2026-07-19T10:03:00.000Z',
  monotonic_ms: 10,
  lat: 50.45,
  lng: -104.62,
  accuracy: 8,
  speed: null,
  heading: null,
  altitude: null,
  source: 'foreground' as const,
  mocked: false,
  is_completion_fix: false,
});

const session = { ride_id: 'ride-1', recording_session_id: 'session-1' };

function createOutbox(points: ReturnType<typeof point>[]) {
  let pending = [...points];
  return {
    startSession: jest.fn(),
    enqueue: jest.fn(),
    listPendingSessions: jest.fn().mockImplementation(async () => (pending.length ? [session] : [])),
    peek: jest.fn().mockImplementation(async () => [...pending]),
    acknowledge: jest.fn().mockImplementation(async (_sessionId: string, ackedThrough: number) => {
      pending = pending.filter((p) => p.sequence_number > ackedThrough);
    }),
    pendingCount: jest.fn().mockImplementation(async () => pending.length),
    closeSession: jest.fn(),
  };
}

describe('flushPendingWithTimeout', () => {
  test('drains the outbox and reports no timeout when the transport responds in time', async () => {
    const outbox = createOutbox([point(1), point(2), point(3)]);
    const recorder = createTripLocationRecorder({ outbox: outbox as any });
    const transport = jest.fn().mockImplementation(async (request) => ({
      recording_session_id: request.recording_session_id,
      acked_through: request.points[request.points.length - 1].sequence_number,
      rejected: [],
    }));

    const result = await recorder.flushPendingWithTimeout(transport, 5_000);

    expect(result).toEqual({
      uploaded_points: 3,
      acknowledged_points: 3,
      skipped: false,
      timedOut: false,
    });
    expect(await outbox.pendingCount('ride-1')).toBe(0);
  });

  test('returns timedOut without blocking when the transport hangs', async () => {
    const outbox = createOutbox([point(1)]);
    const recorder = createTripLocationRecorder({ outbox: outbox as any });
    // Never resolves — simulates a dead network at the dropoff.
    const transport = jest.fn().mockImplementation(() => new Promise(() => {}));

    const started = Date.now();
    const result = await recorder.flushPendingWithTimeout(transport, 50);

    expect(Date.now() - started).toBeLessThan(2_000);
    expect(result.timedOut).toBe(true);
    expect(result.acknowledged_points).toBe(0);
    // Points stay durable for the retention-bounded retry path.
    expect(await outbox.pendingCount('ride-1')).toBe(1);
  });

  test('propagates transport errors so the caller can record them', async () => {
    const outbox = createOutbox([point(1)]);
    const recorder = createTripLocationRecorder({ outbox: outbox as any });
    const transport = jest.fn().mockRejectedValue(new Error('network down'));

    await expect(recorder.flushPendingWithTimeout(transport, 5_000)).rejects.toThrow('network down');
    expect(await outbox.pendingCount('ride-1')).toBe(1);
  });

  test('forces the flush even when below the point threshold and inside the interval', async () => {
    const outbox = createOutbox([point(1)]);
    const recorder = createTripLocationRecorder({ outbox: outbox as any });
    const transport = jest.fn().mockImplementation(async (request) => ({
      recording_session_id: request.recording_session_id,
      acked_through: request.points[request.points.length - 1].sequence_number,
      rejected: [],
    }));

    // A failed attempt primes the interval gate while leaving the point queued.
    await expect(
      recorder.flushPending(jest.fn().mockRejectedValue(new Error('offline'))),
    ).rejects.toThrow('offline');
    const gated = await recorder.flushPending(transport);
    expect(gated.skipped).toBe(true);

    const result = await recorder.flushPendingWithTimeout(transport, 5_000);
    expect(result.skipped).toBe(false);
    expect(transport).toHaveBeenCalledTimes(1);
  });
});
