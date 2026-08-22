/**
 * rideLiveNotification — Android-only ongoing "live ride" notifee
 * notification. Pins: Android-gated availability, the fixed notification id
 * (two callers must share it, never duplicate), and handleFcmData's routing
 * of the backend's `live_activity` data message (end → cancel, otherwise →
 * showOrUpdate with the composed eta+vehicle body).
 */
import { Platform } from 'react-native';

const mockDisplayNotification = jest.fn().mockResolvedValue(undefined);
const mockCancelNotification = jest.fn().mockResolvedValue(undefined);
const mockCreateChannel = jest.fn().mockResolvedValue(undefined);

jest.mock('@notifee/react-native', () => ({
  __esModule: true,
  default: {
    createChannel: (...args: unknown[]) => mockCreateChannel(...args),
    displayNotification: (...args: unknown[]) => mockDisplayNotification(...args),
    cancelNotification: (...args: unknown[]) => mockCancelNotification(...args),
  },
  AndroidImportance: { LOW: 2 },
  AndroidVisibility: { PUBLIC: 1 },
}));

jest.mock('react-native', () => ({
  Platform: { OS: 'android' },
}));

// `available()` inside the module reads `Platform.OS` fresh on every call
// (not cached at import time), so mutating the same Platform object the
// module holds a reference to is enough -- no need to reset/re-require
// the module between tests (doing so would swap in a brand-new mocked
// Platform object via the factory above, decoupling it from this file's
// own `Platform` import and silently ignoring later OS mutations).
function loadModule() {
  return require('../rideLiveNotification');
}

describe('rideLiveNotification (Android)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (Platform as { OS: string }).OS = 'android';
  });

  it('shows a notification with the fixed id and ride-status channel', async () => {
    const { showOrUpdate, RIDE_LIVE_CHANNEL } = loadModule();
    await showOrUpdate({ title: 'Driver on the way', body: '3 min away', rideId: 'ride-1' });

    expect(mockDisplayNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'spinr-ride-live',
        title: 'Driver on the way',
        body: '3 min away',
        data: { rideId: 'ride-1', type: 'ride_status_live' },
        android: expect.objectContaining({ channelId: RIDE_LIVE_CHANNEL, ongoing: true }),
      }),
    );
  });

  it('cancels the same fixed notification id', async () => {
    const { cancel } = loadModule();
    await cancel();
    expect(mockCancelNotification).toHaveBeenCalledWith('spinr-ride-live');
  });

  it('does nothing on a platform other than Android', async () => {
    (Platform as { OS: string }).OS = 'ios';
    const { showOrUpdate, cancel } = loadModule();
    await showOrUpdate({ title: 'x', body: 'y' });
    await cancel();
    expect(mockDisplayNotification).not.toHaveBeenCalled();
    expect(mockCancelNotification).not.toHaveBeenCalled();
  });

  it('swallows a display failure instead of throwing', async () => {
    mockDisplayNotification.mockRejectedValueOnce(new Error('native failure'));
    const { showOrUpdate } = loadModule();
    await expect(showOrUpdate({ title: 'x', body: 'y' })).resolves.toBeUndefined();
  });

  describe('handleFcmData', () => {
    it('returns false and does nothing for a non-live-activity message', async () => {
      const { handleFcmData } = loadModule();
      const handled = await handleFcmData({ type: 'other' });
      expect(handled).toBe(false);
      expect(mockDisplayNotification).not.toHaveBeenCalled();
    });

    it('returns false for undefined data', async () => {
      const { handleFcmData } = loadModule();
      expect(await handleFcmData(undefined)).toBe(false);
    });

    it('cancels the notification on an "end" event', async () => {
      const { handleFcmData } = loadModule();
      const handled = await handleFcmData({ type: 'live_activity', event: 'end' });
      expect(handled).toBe(true);
      expect(mockCancelNotification).toHaveBeenCalledWith('spinr-ride-live');
      expect(mockDisplayNotification).not.toHaveBeenCalled();
    });

    it('shows the notification with eta + vehicle label composed into the body', async () => {
      const { handleFcmData } = loadModule();
      const handled = await handleFcmData({
        type: 'live_activity',
        headline: 'Driver arrived',
        eta_minutes: '4',
        vehicle_label: 'Blue Camry',
        ride_id: 'ride-9',
      });
      expect(handled).toBe(true);
      expect(mockDisplayNotification).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Driver arrived',
          body: '4 min away · Blue Camry',
          data: { rideId: 'ride-9', type: 'ride_status_live' },
        }),
      );
    });

    it('renders "1 min away" (singular) for exactly one minute', async () => {
      const { handleFcmData } = loadModule();
      await handleFcmData({ type: 'live_activity', headline: 'h', eta_minutes: '1' });
      expect(mockDisplayNotification).toHaveBeenCalledWith(
        expect.objectContaining({ body: '1 min away' }),
      );
    });

    it('omits the eta text when eta_minutes is missing or zero', async () => {
      const { handleFcmData } = loadModule();
      await handleFcmData({ type: 'live_activity', headline: 'h', vehicle_label: 'Red Civic' });
      expect(mockDisplayNotification).toHaveBeenCalledWith(
        expect.objectContaining({ body: 'Red Civic' }),
      );
    });

    it('falls back to "Ride update" when no headline is provided', async () => {
      const { handleFcmData } = loadModule();
      await handleFcmData({ type: 'live_activity' });
      expect(mockDisplayNotification).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Ride update' }),
      );
    });
  });
});
