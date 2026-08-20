/**
 * R-P0-1: SOS silent failure — triggerEmergency must rethrow on network error.
 *
 * Pins triggerEmergency in rideStore:
 *   - Calls POST /rides/{rideId}/emergency with lat/lon
 *   - On API failure: THROWS so SOSButton.triggerSOS() can detect failure,
 *     retry, and show "Alert Not Sent" instead of falsely showing "Alert Sent"
 *   - rideStore must NOT show its own Alert — SOSButton owns all failure UX
 *
 * Code under test: rider-app/store/rideStore.ts::triggerEmergency (~line 501)
 */

import { useRideStore } from '../rideStore';
import api from '@shared/api/client';
import { Alert } from 'react-native';

jest.mock('react-native', () => ({
  Platform: { OS: 'ios' },
  Alert: { alert: jest.fn() },
  Linking: { openURL: jest.fn() },
  NativeModules: {},
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('../../config', () => ({
  API_URL: 'http://localhost:8000',
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn(), get: jest.fn(), put: jest.fn(), patch: jest.fn(), delete: jest.fn() },
}));

jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: { getState: jest.fn(() => ({ user: null, token: null })) },
}));

const mockPost = api.post as jest.Mock;
const mockAlert = Alert.alert as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('rideStore — triggerEmergency (P2-14 / R13)', () => {
  it('calls POST /rides/{rideId}/emergency with message and lat/lon', async () => {
    mockPost.mockResolvedValue({ data: { success: true, incident_id: 'inc-001' } });

    await useRideStore.getState().triggerEmergency('ride-001', 43.651, -79.347);

    expect(mockPost).toHaveBeenCalledWith('/rides/ride-001/emergency', {
      message: 'Emergency assistance requested via app button',
      latitude: 43.651,
      longitude: -79.347,
    });
  });

  it('calls POST without lat/lon when not provided', async () => {
    mockPost.mockResolvedValue({ data: { success: true, incident_id: 'inc-002' } });

    await useRideStore.getState().triggerEmergency('ride-002');

    expect(mockPost).toHaveBeenCalledWith('/rides/ride-002/emergency', {
      message: 'Emergency assistance requested via app button',
      latitude: undefined,
      longitude: undefined,
    });
  });

  it('on API failure throws so SOSButton can detect failure and retry', async () => {
    const err = new Error('Network error');
    mockPost.mockRejectedValue(err);

    // Use try/catch instead of .rejects.toThrow() — the latter's internal Jest
    // assertion wrapper throws in the wrong execution context and can produce
    // a fatal uncaught exception on Node 22 in CI even when the test passes.
    let caughtErr: unknown;
    try {
      await useRideStore.getState().triggerEmergency('ride-003', 43.0, -79.0);
      throw new Error('Expected triggerEmergency to throw but it resolved');
    } catch (e) {
      caughtErr = e;
    }

    // triggerEmergency must rethrow — SOSButton's retry loop catches this to
    // determine backendOk=false and show "Alert Not Sent" instead of "Alert Sent"
    expect((caughtErr as Error).message).toBe('Network error');

    // rideStore must NOT show its own Alert — that's SOSButton's responsibility
    expect(mockAlert).not.toHaveBeenCalled();
  });

  // --- Regression: the nested-retry multiplication (analysis finding S2) ---

  it('issues exactly ONE POST per call — retry policy lives only in SOSButton', async () => {
    // rideStore used to retry 3x internally. SOSButton retries 3x around it,
    // so a single press could fire up to 9 real POSTs — each able to insert
    // its own safety_incidents row and re-blast "URGENT" SMS to every
    // emergency contact. Exactly one POST per call is the invariant.
    const err = new Error('Network error');
    mockPost.mockRejectedValue(err);

    await expect(
      useRideStore.getState().triggerEmergency('ride-004', 43.0, -79.0),
    ).rejects.toThrow('Network error');

    expect(mockPost).toHaveBeenCalledTimes(1);
  });

  it('returns the response body so the caller can report real contact status', async () => {
    // Previously `return;` — the body was discarded and SOSButton claimed
    // "your emergency contacts have been notified" regardless of the truth.
    mockPost.mockResolvedValue({
      data: {
        success: true,
        incident_id: 'inc-005',
        contacts_notified: 1,
        contacts: [
          { id: 'c1', name: 'Jane', notified: true },
          { id: 'c2', name: 'Sam', notified: false },
        ],
      },
    });

    const result = await useRideStore.getState().triggerEmergency('ride-005');

    expect(result.incident_id).toBe('inc-005');
    expect(result.contacts_notified).toBe(1);
    expect(result.contacts).toHaveLength(2);
    expect(result.contacts[1].notified).toBe(false);
  });
});

// --- Ride-less SOS (ACTION_ITEMS.md B15(c)) ---
// Same contract as triggerEmergency above, minus rideId. Code under test:
// rider-app/store/rideStore.ts::triggerRidelessEmergency.

describe('rideStore — triggerRidelessEmergency (ACTION_ITEMS.md B15(c))', () => {
  it('calls POST /rides/emergency (no ride_id segment) with message and lat/lon', async () => {
    mockPost.mockResolvedValue({ data: { success: true, incident_id: 'inc-rl-001' } });

    await useRideStore.getState().triggerRidelessEmergency(43.651, -79.347);

    expect(mockPost).toHaveBeenCalledWith('/rides/emergency', {
      message: 'Emergency assistance requested via app button',
      latitude: 43.651,
      longitude: -79.347,
    });
  });

  it('on API failure (e.g. 404 when rideless_sos_enabled is off) throws so SOSButton can show its failure UX', async () => {
    const err = new Error('Not found');
    mockPost.mockRejectedValue(err);

    let caughtErr: unknown;
    try {
      await useRideStore.getState().triggerRidelessEmergency(43.0, -79.0);
      throw new Error('Expected triggerRidelessEmergency to throw but it resolved');
    } catch (e) {
      caughtErr = e;
    }

    expect((caughtErr as Error).message).toBe('Not found');
    expect(mockAlert).not.toHaveBeenCalled();
  });

  it('issues exactly ONE POST per call — same no-nested-retry invariant as triggerEmergency', async () => {
    const err = new Error('Network error');
    mockPost.mockRejectedValue(err);

    await expect(
      useRideStore.getState().triggerRidelessEmergency(43.0, -79.0),
    ).rejects.toThrow('Network error');

    expect(mockPost).toHaveBeenCalledTimes(1);
  });

  it('returns the response body so the caller can report real contact status', async () => {
    mockPost.mockResolvedValue({
      data: {
        success: true,
        incident_id: 'inc-rl-002',
        contacts_notified: 0,
        contacts: [],
        notification_warning: 'Emergency contacts could not be reached — please call them directly.',
      },
    });

    const result = await useRideStore.getState().triggerRidelessEmergency();

    expect(result.incident_id).toBe('inc-rl-002');
    expect(result.notification_warning).toBeTruthy();
  });
});
