/**
 * P2-14: SOS E2E — rider store (R13)
 *
 * Pins triggerEmergency in rideStore:
 *   - Calls POST /rides/{rideId}/emergency with lat/lon
 *   - On API failure: shows Alert and rethrows (so the caller can 911-prompt)
 *
 * Code under test: rider-app/store/rideStore.ts::triggerEmergency (~line 496)
 */

const mockPost = jest.fn();
const mockAlert = jest.fn();
const mockOpenURL = jest.fn();

jest.mock('react-native', () => ({
  Platform: { OS: 'ios' },
  Alert: { alert: mockAlert },
  Linking: { openURL: mockOpenURL },
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('../../config', () => ({
  API_URL: 'http://localhost:8000',
}));

jest.mock('../../api/client', () => ({
  __esModule: true,
  default: { post: mockPost, get: jest.fn() },
}));

import { useRideStore } from '../rideStore';

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

  it('on API failure shows Alert and rethrows error', async () => {
    const err = new Error('Network error');
    mockPost.mockRejectedValue(err);

    await expect(
      useRideStore.getState().triggerEmergency('ride-003', 43.0, -79.0)
    ).rejects.toThrow('Network error');

    expect(mockAlert).toHaveBeenCalledWith(
      expect.stringContaining('Emergency'),
      expect.any(String),
      expect.any(Array),
    );
  });
});
