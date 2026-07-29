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
