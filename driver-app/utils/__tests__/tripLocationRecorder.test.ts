/* eslint-disable import/first */

jest.mock('expo-location', () => ({
  getCurrentPositionAsync: jest.fn(),
  Accuracy: { High: 6, Balanced: 4 },
}));

// The recorder creates a module-level default outbox, even though these tests
// inject a fake one. Keep that import on the same lightweight seam used by the
// working background recorder tests.
jest.mock('../tripLocationOutbox', () => ({
  tripLocationOutbox: {},
}));

import * as Location from 'expo-location';
import { createTripLocationRecorder } from '../tripLocationRecorder';

const getCurrentPositionAsync = Location.getCurrentPositionAsync as jest.Mock;

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
    quarantineSession: jest.fn().mockResolvedValue(undefined),
    pendingCount: jest.fn(),
    closeSession: jest.fn(),
  };
}

beforeEach(() => {
  getCurrentPositionAsync.mockReset();
});

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
    expect(outbox.acknowledge).toHaveBeenCalledWith(
      'session-1',
      4,
      { from: 4, to: 4 },
      [{ sequence_number: 3, reason: 'invalid_coordinate' }],
    );
  });

  test('a completion acknowledgement deletes only the completion fix, not the queued tail', async () => {
    const outbox = createOutbox();
    // Session has an un-uploaded in-ride tail (seq 3) below the completion fix (seq 4).
    const tail = { ...point, sequence_number: 3, is_completion_fix: false };
    outbox.peek.mockResolvedValue([tail, point]);
    const recorder = createTripLocationRecorder({ outbox: outbox as any });

    const acknowledged = await recorder.applyAcknowledgement({
      recording_session_id: 'session-1',
      acked_through: 4,
    });

    // Only the completion point is licensed for deletion; the tail survives.
    expect(acknowledged).toBe(1);
    expect(outbox.acknowledge).toHaveBeenCalledWith('session-1', 4, { from: 4, to: 4 }, []);
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

describe('TripLocationRecorder completion fix capture', () => {
  test('resolves without a fix when the GPS request hangs past the timeout', async () => {
    jest.useFakeTimers();
    try {
      getCurrentPositionAsync.mockReturnValue(new Promise(() => {})); // never resolves
      const outbox = createOutbox();
      outbox.pendingCount.mockResolvedValue(7);
      const recorder = createTripLocationRecorder({ outbox: outbox as any });

      const resultPromise = recorder.captureCompletionFix('ride-1');
      await jest.advanceTimersByTimeAsync(10_000);
      const result = await resultPromise;

      expect(result.point).toBeNull();
      expect(result.pendingCount).toBe(7);
      expect(outbox.enqueue).not.toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });

  test('still completes when the local outbox read fails', async () => {
    getCurrentPositionAsync.mockResolvedValue({
      timestamp: 1_700_000_000_000,
      coords: { latitude: 50.45, longitude: -104.62, accuracy: 5, speed: null, heading: null, altitude: null },
    });
    const outbox = createOutbox();
    outbox.enqueue.mockResolvedValue(point);
    outbox.pendingCount.mockRejectedValue(new Error('disk corrupt'));
    const recorder = createTripLocationRecorder({ outbox: outbox as any });

    const result = await recorder.captureCompletionFix('ride-1');

    expect(result.pendingCount).toBe(0); // degrades safely; completion never blocked
  });
});

describe('TripLocationRecorder flush resilience', () => {
  const sessionPoint = (recording_session_id: string, ride_id: string) => ({
    ...point,
    recording_session_id,
    ride_id,
    sequence_number: 0,
  });

  test('quarantines a permanently rejected session and keeps uploading the rest', async () => {
    const outbox = createOutbox();
    outbox.listPendingSessions.mockResolvedValue([
      { recording_session_id: 'session-A', ride_id: 'ride-A', opened_at: 'a', closed_at: null },
      { recording_session_id: 'session-B', ride_id: 'ride-B', opened_at: 'b', closed_at: null },
    ]);
    let bPeeks = 0;
    outbox.peek.mockImplementation(async (sessionId: string) => {
      if (sessionId === 'session-A') return [sessionPoint('session-A', 'ride-A')];
      if (sessionId === 'session-B') { bPeeks += 1; return bPeeks === 1 ? [sessionPoint('session-B', 'ride-B')] : []; }
      return [];
    });
    const transport = jest.fn(async (request: { recording_session_id: string }) => {
      if (request.recording_session_id === 'session-A') {
        const error: any = new Error('ride no longer active');
        error.response = { status: 409 };
        throw error;
      }
      return { recording_session_id: 'session-B', acked_through: 0 };
    });
    const recorder = createTripLocationRecorder({ outbox: outbox as any });

    const result = await recorder.flushPending(transport, { force: true });

    expect(outbox.quarantineSession).toHaveBeenCalledWith('session-A', 'http_409');
    expect(transport).toHaveBeenCalledWith(expect.objectContaining({ recording_session_id: 'session-B' }));
    expect(result.skipped).toBe(false);
  });

  test('stops and retains the session on a retryable failure', async () => {
    const outbox = createOutbox();
    outbox.listPendingSessions.mockResolvedValue([
      { recording_session_id: 'session-A', ride_id: 'ride-A', opened_at: 'a', closed_at: null },
    ]);
    outbox.peek.mockResolvedValue([sessionPoint('session-A', 'ride-A')]);
    const transport = jest.fn(async () => { throw new Error('network down'); });
    const recorder = createTripLocationRecorder({ outbox: outbox as any });

    await expect(recorder.flushPending(transport, { force: true })).rejects.toThrow('network down');
    expect(outbox.quarantineSession).not.toHaveBeenCalled();
  });
});
