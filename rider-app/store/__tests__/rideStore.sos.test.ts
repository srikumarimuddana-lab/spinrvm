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
  useAuthStore: { getState: jest.fn(() => ({ user: null, token: null })) },
}));

import { useRideStore } from '../rideStore';
import api from '@shared/api/client';
import { Alert, Linking } from 'react-native';

const mockPost = api.post as jest.Mock;
const mockAlert = Alert.alert as jest.Mock;
const mockOpenURL = Linking.openURL as jest.Mock;

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

    // triggerEmergency must rethrow — SOSButton's retry loop catches this to
    // determine backendOk=false and show "Alert Not Sent" instead of "Alert Sent"
    await expect(
      useRideStore.getState().triggerEmergency('ride-003', 43.0, -79.0),
    ).rejects.toThrow('Network error');

    // rideStore must NOT show its own Alert — that's SOSButton's responsibility
    expect(mockAlert).not.toHaveBeenCalled();
  });
});
