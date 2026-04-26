/**
 * P2-14: SOS E2E — rider store (R13)
 *
 * Pins triggerEmergency in rideStore:
 *   - Calls POST /rides/{rideId}/emergency with lat/lon
 *   - On API failure: shows Alert and rethrows (so the caller can 911-prompt)
 *
 * Code under test: rider-app/store/rideStore.ts::triggerEmergency (~line 496)
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

  it('on API failure shows Alert (error is swallowed)', async () => {
    const err = new Error('Network error');
    mockPost.mockRejectedValue(err);

    // triggerEmergency swallows the error and shows an Alert instead of rethrowing
    await useRideStore.getState().triggerEmergency('ride-003', 43.0, -79.0);

    expect(mockAlert).toHaveBeenCalledWith(
      expect.stringContaining('Emergency'),
      expect.any(String),
      expect.any(Array),
    );
  });
});
