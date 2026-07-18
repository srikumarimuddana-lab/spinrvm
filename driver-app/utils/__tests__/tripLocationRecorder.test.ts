/* eslint-disable import/first */

jest.mock('expo-location', () => ({}));

// The recorder creates a module-level default outbox, even though these tests
// inject a fake one. Keep that import on the same lightweight seam used by the
// working background recorder tests.
jest.mock('../tripLocationOutbox', () => ({
  tripLocationOutbox: {},
}));

import { createTripLocationRecorder } from '../tripLocationRecorder';

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
