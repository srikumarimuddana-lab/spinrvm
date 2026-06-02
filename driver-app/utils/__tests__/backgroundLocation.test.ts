/**
 * Background-location cadence control — trip-distance capture fix.
 *
 * Pins that updateBackgroundLocationCadence re-tunes the *running* background
 * task to the dense TRIP_CADENCE during a trip (and is a no-op when the task
 * isn't registered). This is what keeps a backgrounded trip well-sampled now
 * that POST /drivers/location-batch persists every point as a breadcrumb — the
 * foreground watchPositionAsync stops firing the moment the app backgrounds.
 */

const mockStartUpdates = jest.fn((..._args: any[]) => Promise.resolve());
let mockTaskRegistered = true;
let mockBgPermission = 'granted';

jest.mock('expo-location', () => ({
  startLocationUpdatesAsync: (...args: any[]) => mockStartUpdates(...args),
  stopLocationUpdatesAsync: jest.fn(() => Promise.resolve()),
  getBackgroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: mockBgPermission })),
  requestBackgroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'granted' })),
  getCurrentPositionAsync: jest.fn(() => Promise.resolve(null)),
  startGeofencingAsync: jest.fn(() => Promise.resolve()),
  stopGeofencingAsync: jest.fn(() => Promise.resolve()),
  Accuracy: { High: 6, Balanced: 4 },
  ActivityType: { AutomotiveNavigation: 3 },
  GeofencingEventType: { Enter: 1, Exit: 2 },
}));

jest.mock('expo-task-manager', () => ({
  defineTask: jest.fn(),
  isTaskRegisteredAsync: jest.fn(() => Promise.resolve(mockTaskRegistered)),
}));

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(() => Promise.resolve(null)),
  setItemAsync: jest.fn(() => Promise.resolve()),
  deleteItemAsync: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/config', () => ({ API_URL: 'https://example.test' }), { virtual: true });

import {
  updateBackgroundLocationCadence,
  setBackgroundTripActive,
  TRIP_CADENCE,
  IDLE_CADENCE,
} from '../backgroundLocation';
import * as SecureStore from 'expo-secure-store';

describe('setBackgroundTripActive (killed-app recovery cadence)', () => {
  beforeEach(() => {
    (SecureStore.setItemAsync as jest.Mock).mockClear();
    (SecureStore.deleteItemAsync as jest.Mock).mockClear();
  });

  it('persists the trip flag when a ride is active', async () => {
    await setBackgroundTripActive(true);
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith('spinr_bg_trip_active', 'true');
  });

  it('clears the trip flag when idle', async () => {
    await setBackgroundTripActive(false);
    expect(SecureStore.deleteItemAsync).toHaveBeenCalledWith('spinr_bg_trip_active');
  });
});

describe('updateBackgroundLocationCadence', () => {
  beforeEach(() => {
    mockStartUpdates.mockClear();
    mockTaskRegistered = true;
    mockBgPermission = 'granted';
  });

  it('re-tunes the running task to dense TRIP_CADENCE', async () => {
    await updateBackgroundLocationCadence(TRIP_CADENCE);
    expect(mockStartUpdates).toHaveBeenCalledTimes(1);
    const opts = mockStartUpdates.mock.calls[0][1];
    expect(opts.timeInterval).toBe(TRIP_CADENCE.timeInterval); // 4000ms
    expect(opts.distanceInterval).toBe(TRIP_CADENCE.distanceInterval); // 10m
    expect(opts.accuracy).toBe(TRIP_CADENCE.accuracy); // High
  });

  it('relaxes back to coarse IDLE_CADENCE', async () => {
    await updateBackgroundLocationCadence(IDLE_CADENCE);
    const opts = mockStartUpdates.mock.calls[0][1];
    expect(opts.timeInterval).toBe(IDLE_CADENCE.timeInterval); // 30000ms
    expect(opts.distanceInterval).toBe(IDLE_CADENCE.distanceInterval); // 50m
  });

  it('is a no-op when the background task is not registered', async () => {
    mockTaskRegistered = false;
    await updateBackgroundLocationCadence(TRIP_CADENCE);
    expect(mockStartUpdates).not.toHaveBeenCalled();
  });

  it('is a no-op when background permission was revoked', async () => {
    mockBgPermission = 'denied';
    await updateBackgroundLocationCadence(TRIP_CADENCE);
    expect(mockStartUpdates).not.toHaveBeenCalled();
  });

  it('TRIP_CADENCE samples denser than IDLE_CADENCE', () => {
    expect(TRIP_CADENCE.timeInterval!).toBeLessThan(IDLE_CADENCE.timeInterval!);
    expect(TRIP_CADENCE.distanceInterval!).toBeLessThan(IDLE_CADENCE.distanceInterval!);
  });
});
