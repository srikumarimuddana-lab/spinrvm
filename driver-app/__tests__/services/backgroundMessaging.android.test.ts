/**
 * backgroundMessaging.ts's Android-only paths -- everything the existing
 * backgroundMessaging.test.ts deliberately stays off of (that file pins
 * Platform.OS='ios' specifically to exercise the persist+republish path
 * without Notifee). This file covers what's left:
 *
 *   - offerDisplayDataFromFcm: the pure FCM-payload -> Notifee-content
 *     mapper (shared by this handler and the foreground listener).
 *   - the Android branch of the message handler: renders the Uber-style
 *     notification via displayRideOfferNotification, respecting the
 *     driver's muted-sound preference.
 *   - the location_health branch: re-asserts the background location task
 *     when the server-side GPS gap monitor pings this handler.
 *   - notifee.onBackgroundEvent's accept/decline/tap routing -- this is
 *     the ONLY place a lock-screen decline reaches the backend while the
 *     app is killed (per the file's own comment: an undelivered decline
 *     costs the driver a strike toward auto-offline), so its success/
 *     failure branches matter for real.
 *
 * Platform.OS is set to 'android' via a stable mock object BEFORE the
 * module is first required in each test -- unlike the other service
 * files in this app, backgroundMessaging.ts reads Platform.OS in a
 * top-level `if` at module-load time (to decide whether to require
 * notifee/notifeeService at all), not inside each function, so the
 * Platform mutation must land before import, every time, hence
 * jest.resetModules() + a fresh require() per test rather than one
 * shared import.
 */

const mockPlatform = { OS: 'android' };
jest.mock('react-native', () => ({ Platform: mockPlatform }));

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'https://api.spinr.test' },
}));

const mockSetBackgroundMessageHandler = jest.fn();
const mockGetAppCheckToken = jest.fn(() => Promise.resolve(null));
const mockInitFirebaseServices = jest.fn(() => Promise.resolve());
jest.mock('@shared/services/firebase', () => ({
  setBackgroundMessageHandler: (h: unknown) => mockSetBackgroundMessageHandler(h),
  getAppCheckToken: (...a: unknown[]) => mockGetAppCheckToken(...a),
  initFirebaseServices: (...a: unknown[]) => mockInitFirebaseServices(...a),
}));

const mockSetItem = jest.fn(() => Promise.resolve());
const mockRemoveItem = jest.fn(() => Promise.resolve());
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    setItem: (...a: unknown[]) => mockSetItem(...a),
    removeItem: (...a: unknown[]) => mockRemoveItem(...a),
  },
}));

const mockGetBackgroundAuthToken = jest.fn(() => Promise.resolve('driver-token'));
jest.mock('../../utils/backgroundLocation', () => ({
  getBackgroundAuthToken: (...a: unknown[]) => mockGetBackgroundAuthToken(...a),
  recoverTripLocation: jest.fn(() => Promise.resolve()),
}));

const mockOnBackgroundEvent = jest.fn();
jest.mock('@notifee/react-native', () => ({
  __esModule: true,
  default: { onBackgroundEvent: (h: unknown) => mockOnBackgroundEvent(h) },
}));

const mockParseRideOfferEvent = jest.fn();
const mockDisplayRideOfferNotification = jest.fn(() => Promise.resolve());
const mockDismissRideOfferNotification = jest.fn(() => Promise.resolve());
jest.mock('../../services/notifeeService', () => ({
  parseRideOfferEvent: (...a: unknown[]) => mockParseRideOfferEvent(...a),
  displayRideOfferNotification: (...a: unknown[]) => mockDisplayRideOfferNotification(...a),
  dismissRideOfferNotification: (...a: unknown[]) => mockDismissRideOfferNotification(...a),
}));

const mockLoadAlertPrefs = jest.fn(() => Promise.resolve());
let mockSoundEffects = true;
jest.mock('../../store/alertPrefsStore', () => ({
  useAlertPrefsStore: {
    getState: () => ({
      get soundEffects() {
        return mockSoundEffects;
      },
      loadAlertPrefs: mockLoadAlertPrefs,
    }),
  },
}));

const mockFetch = jest.fn();
(global as any).fetch = mockFetch;

const OFFER = {
  type: 'new_ride_assignment',
  ride_id: 'r1',
  pickup_address: '1 Main St',
  dropoff_address: '2 Side Rd',
  fare: '14.50',
  countdown_seconds: '20',
};

function loadModule() {
  jest.resetModules();
  return require('../../services/backgroundMessaging');
}

describe('backgroundMessaging (Android)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockPlatform.OS = 'android';
    mockSoundEffects = true;
    mockFetch.mockReset();
    jest.spyOn(console, 'warn').mockImplementation(() => {});
    jest.spyOn(console, 'log').mockImplementation(() => {});
  });
  afterEach(() => jest.restoreAllMocks());

  describe('offerDisplayDataFromFcm', () => {
    it('returns null when the payload is not a ride offer', () => {
      const { offerDisplayDataFromFcm } = loadModule();
      expect(offerDisplayDataFromFcm({ type: 'chat_message', ride_id: 'r1' })).toBeNull();
    });

    it('returns null when ride_id is missing', () => {
      const { offerDisplayDataFromFcm } = loadModule();
      expect(offerDisplayDataFromFcm({ type: 'new_ride_assignment' })).toBeNull();
    });

    it('maps a full FCM payload, coercing numeric strings', () => {
      const { offerDisplayDataFromFcm } = loadModule();
      const result = offerDisplayDataFromFcm({
        type: 'new_ride_assignment',
        ride_id: 'r1',
        fare: '14.5',
        total_bonus: '2',
        distance_km: '3.2',
        rider_rating: '4.9',
        incentives: '[{"id":1},{"id":2}]',
      });
      expect(result).toMatchObject({
        ride_id: 'r1',
        fare: 14.5,
        total_bonus: 2,
        distance_km: 3.2,
        rider_rating: 4.9,
        incentives_count: 2,
      });
    });

    it('falls back to 0 fare/bonus and undefined optional fields when absent', () => {
      const { offerDisplayDataFromFcm } = loadModule();
      const result = offerDisplayDataFromFcm({ type: 'new_ride_assignment', ride_id: 'r1' });
      expect(result).toMatchObject({ fare: 0, total_bonus: 0, incentives_count: 0 });
      expect(result!.distance_km).toBeUndefined();
    });
  });

  describe('Android message handler', () => {
    it('renders the Notifee notification for a new ride assignment', async () => {
      const { registerBackgroundMessageHandlers } = loadModule();
      registerBackgroundMessageHandlers();
      const handler = mockSetBackgroundMessageHandler.mock.calls[0][0];

      await handler({ data: OFFER });

      expect(mockLoadAlertPrefs).toHaveBeenCalled();
      expect(mockDisplayRideOfferNotification).toHaveBeenCalledWith(
        expect.objectContaining({ ride_id: 'r1' }),
        undefined, // soundEffects=true -> not muted -> no opts object
      );
    });

    it('passes {muted: true} when the driver has sound effects off', async () => {
      mockSoundEffects = false;
      const { registerBackgroundMessageHandlers } = loadModule();
      registerBackgroundMessageHandlers();
      const handler = mockSetBackgroundMessageHandler.mock.calls[0][0];

      await handler({ data: OFFER });

      expect(mockDisplayRideOfferNotification).toHaveBeenCalledWith(
        expect.objectContaining({ ride_id: 'r1' }),
        { muted: true },
      );
    });

    it('does not throw and still persists the offer when displayRideOfferNotification rejects', async () => {
      mockDisplayRideOfferNotification.mockRejectedValueOnce(new Error('native failure'));
      const { registerBackgroundMessageHandlers } = loadModule();
      registerBackgroundMessageHandlers();
      const handler = mockSetBackgroundMessageHandler.mock.calls[0][0];

      await expect(handler({ data: OFFER })).resolves.toBeUndefined();
      expect(mockSetItem).toHaveBeenCalledTimes(1);
    });

    it('re-asserts the background location task on a location_health ping', async () => {
      const { registerBackgroundMessageHandlers } = loadModule();
      registerBackgroundMessageHandlers();
      const handler = mockSetBackgroundMessageHandler.mock.calls[0][0];
      const bg = require('../../utils/backgroundLocation');

      await handler({ data: { type: 'location_health' } });

      expect(bg.recoverTripLocation).toHaveBeenCalled();
      // No offer path touched for this message type.
      expect(mockDisplayRideOfferNotification).not.toHaveBeenCalled();
      expect(mockSetItem).not.toHaveBeenCalled();
    });

    it('does not throw when the location-health recovery itself fails', async () => {
      const { registerBackgroundMessageHandlers } = loadModule();
      registerBackgroundMessageHandlers();
      const handler = mockSetBackgroundMessageHandler.mock.calls[0][0];
      const bg = require('../../utils/backgroundLocation');
      bg.recoverTripLocation.mockRejectedValueOnce(new Error('boom'));

      await expect(handler({ data: { type: 'location_health' } })).resolves.toBeUndefined();
    });
  });

  describe('notifee.onBackgroundEvent routing', () => {
    async function fireEvent(parsed: unknown) {
      const { registerBackgroundMessageHandlers } = loadModule();
      registerBackgroundMessageHandlers();
      mockParseRideOfferEvent.mockReturnValue(parsed);
      const onEvent = mockOnBackgroundEvent.mock.calls[0][0];
      await onEvent({});
    }

    it('does nothing for an event that is not ours or carries no ride_id', async () => {
      await fireEvent(null);
      expect(mockDismissRideOfferNotification).not.toHaveBeenCalled();
      expect(mockRemoveItem).not.toHaveBeenCalled();
    });

    it('accept: clears the pending offer, stashes the accept action, then dismisses', async () => {
      await fireEvent({ action: 'accept', ride_id: 'r1' });

      expect(mockRemoveItem).toHaveBeenCalledWith('spinr_pending_ride_offer');
      expect(mockSetItem).toHaveBeenCalledWith(
        'spinr_pending_notifee_action',
        expect.stringContaining('"action":"accept"'),
      );
      expect(mockDismissRideOfferNotification).toHaveBeenCalled();
    });

    it('decline: clears the offer, tries the headless decline, and dismisses when it succeeds', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, status: 200 });
      await fireEvent({ action: 'decline', ride_id: 'r1' });

      expect(mockRemoveItem).toHaveBeenCalledWith('spinr_pending_ride_offer');
      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.spinr.test/api/v1/drivers/rides/r1/decline',
        expect.objectContaining({ method: 'POST' }),
      );
      // Delivered -> no fallback stash of the decline action needed.
      expect(mockSetItem).not.toHaveBeenCalledWith(
        'spinr_pending_notifee_action',
        expect.anything(),
      );
      expect(mockDismissRideOfferNotification).toHaveBeenCalled();
    });

    it('decline: treats a 409 (already reassigned) as terminal -- delivered, no stash', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 409 });
      await fireEvent({ action: 'decline', ride_id: 'r1' });

      expect(mockSetItem).not.toHaveBeenCalledWith(
        'spinr_pending_notifee_action',
        expect.anything(),
      );
    });

    it('decline: stashes the action for retry when the backend call fails (5xx)', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 503 });
      await fireEvent({ action: 'decline', ride_id: 'r1' });

      expect(mockSetItem).toHaveBeenCalledWith(
        'spinr_pending_notifee_action',
        expect.stringContaining('"action":"decline"'),
      );
      expect(mockDismissRideOfferNotification).toHaveBeenCalled();
    });

    it('decline: stashes the action when there is no auth token at all', async () => {
      mockGetBackgroundAuthToken.mockResolvedValueOnce(null);
      await fireEvent({ action: 'decline', ride_id: 'r1' });

      expect(mockFetch).not.toHaveBeenCalled();
      expect(mockSetItem).toHaveBeenCalledWith(
        'spinr_pending_notifee_action',
        expect.stringContaining('"action":"decline"'),
      );
    });

    it('decline: stashes the action on a network error', async () => {
      mockFetch.mockRejectedValueOnce(new Error('offline'));
      await fireEvent({ action: 'decline', ride_id: 'r1' });

      expect(mockSetItem).toHaveBeenCalledWith(
        'spinr_pending_notifee_action',
        expect.stringContaining('"action":"decline"'),
      );
    });

    it('a bare notification tap stashes the tap action WITHOUT clearing the pending offer', async () => {
      await fireEvent({ action: 'tap', ride_id: 'r1' });

      expect(mockRemoveItem).not.toHaveBeenCalled();
      expect(mockSetItem).toHaveBeenCalledWith(
        'spinr_pending_notifee_action',
        expect.stringContaining('"action":"tap"'),
      );
      expect(mockDismissRideOfferNotification).toHaveBeenCalled();
    });

    it('does not throw when the whole handler body fails unexpectedly', async () => {
      mockRemoveItem.mockRejectedValueOnce(new Error('storage exploded'));
      // _clearPendingOffer swallows its own errors, so this exercises that
      // guard rather than the outer catch -- still must not throw.
      await expect(fireEvent({ action: 'accept', ride_id: 'r1' })).resolves.toBeUndefined();
    });
  });
});
