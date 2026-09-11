/**
 * notifeeService — the driver's "incoming call" style ride-offer
 * notification. Pins: ensureNotifeeReady's Android-vs-iOS channel/category
 * setup and its once-only idempotency, displayRideOfferNotification's
 * timeout resolution and its two-tier fallback (a failed rich render must
 * still surface a basic, actionable notification rather than nothing),
 * dismissRideOfferNotification's cleanup, and parseRideOfferEvent's event
 * routing (accept/decline/tap, and the "not our notification" no-match).
 *
 * Code under test: driver-app/services/notifeeService.ts
 */
import { EventType } from '@notifee/react-native';

const mockCreateChannel = jest.fn().mockResolvedValue(undefined);
const mockDeleteChannel = jest.fn().mockResolvedValue(undefined);
const mockRequestPermission = jest.fn().mockResolvedValue(undefined);
const mockSetNotificationCategories = jest.fn().mockResolvedValue(undefined);
const mockDisplayNotification = jest.fn().mockResolvedValue(undefined);
const mockCancelNotification = jest.fn().mockResolvedValue(undefined);

jest.mock('@notifee/react-native', () => ({
  __esModule: true,
  default: {
    createChannel: (...a: unknown[]) => mockCreateChannel(...a),
    deleteChannel: (...a: unknown[]) => mockDeleteChannel(...a),
    requestPermission: (...a: unknown[]) => mockRequestPermission(...a),
    setNotificationCategories: (...a: unknown[]) => mockSetNotificationCategories(...a),
    displayNotification: (...a: unknown[]) => mockDisplayNotification(...a),
    cancelNotification: (...a: unknown[]) => mockCancelNotification(...a),
  },
  AndroidCategory: { CALL: 'call' },
  AndroidColor: { GREEN: 'green' },
  AndroidImportance: { HIGH: 4, DEFAULT: 3 },
  AndroidVisibility: { PUBLIC: 1 },
  EventType: { ACTION_PRESS: 2, PRESS: 1 },
}));

// A stable object reference the factory always returns, so mutating
// `.OS` here still takes effect after jest.resetModules() forces
// react-native's mock factory to be re-invoked (each test needs
// resetModules to clear the module's own channelReadyPromise/timer
// singleton state -- if the factory instead returned a fresh literal
// each time, that fresh object would silently ignore any OS mutation
// made against the test file's original `Platform` reference).
const mockPlatform = { OS: 'android' };
jest.mock('react-native', () => ({ Platform: mockPlatform }));

const BASE_OFFER = {
  ride_id: 'ride-1',
  fare: 12.5,
};

describe('notifeeService', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.resetModules();
    mockPlatform.OS = 'android';
    // displayRideOfferNotification always schedules a real 15s dismiss
    // timer as a side effect -- fake timers keep it from dangling past
    // the test file's own teardown (a real pending timer otherwise makes
    // Jest warn "worker process failed to exit gracefully").
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  describe('ensureNotifeeReady', () => {
    it('creates both Android channels, prunes stale ones, and requests permission', async () => {
      const { ensureNotifeeReady } = require('../../services/notifeeService');
      await ensureNotifeeReady();

      expect(mockCreateChannel).toHaveBeenCalledTimes(2);
      expect(mockCreateChannel).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'ride-offers-v3' }),
      );
      expect(mockCreateChannel).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'ride-offers-fg-v2' }),
      );
      expect(mockDeleteChannel).toHaveBeenCalledWith('ride-offers-v2');
      expect(mockDeleteChannel).toHaveBeenCalledWith('ride-offers-fg-v1');
      expect(mockRequestPermission).toHaveBeenCalledWith();
      expect(mockSetNotificationCategories).not.toHaveBeenCalled();
    });

    it('registers the iOS category with Accept/Decline instead of creating channels', async () => {
      mockPlatform.OS = 'ios';
      const { ensureNotifeeReady } = require('../../services/notifeeService');
      await ensureNotifeeReady();

      expect(mockCreateChannel).not.toHaveBeenCalled();
      expect(mockRequestPermission).toHaveBeenCalledWith(
        expect.objectContaining({ alert: true, sound: true, badge: true }),
      );
      expect(mockSetNotificationCategories).toHaveBeenCalledWith([
        expect.objectContaining({
          id: 'ride-offer',
          actions: [
            expect.objectContaining({ id: 'accept' }),
            expect.objectContaining({ id: 'decline' }),
          ],
        }),
      ]);
    });

    it('is idempotent -- only sets up once even when called concurrently', async () => {
      const { ensureNotifeeReady } = require('../../services/notifeeService');
      await Promise.all([ensureNotifeeReady(), ensureNotifeeReady(), ensureNotifeeReady()]);
      expect(mockCreateChannel).toHaveBeenCalledTimes(2);
    });
  });

  describe('displayRideOfferNotification', () => {
    it('renders the rich Android notification with the fare in the title', async () => {
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification({ ...BASE_OFFER, fare: 12.5, total_bonus: 2.5 });

      expect(mockDisplayNotification).toHaveBeenCalledTimes(1);
      const req = mockDisplayNotification.mock.calls[0][0];
      expect(req.title).toBe('New ride · $15.00');
      expect(req.id).toBe('ride-offer-current');
    });

    it('routes to the silent channel and skips full-screen intent when silent', async () => {
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification(BASE_OFFER, { silent: true });

      const req = mockDisplayNotification.mock.calls[0][0];
      expect(req.android.channelId).toBe('ride-offers-fg-v2');
      expect(req.android.fullScreenAction).toBeUndefined();
      expect(req.android.sound).toBeUndefined();
    });

    // The overlapping-ringtone regression. loopSound sets FLAG_INSISTENT, which
    // Notifee documents as repeating until the notification is CANCELLED — an
    // update to the silent channel is not a cancel, so the handover has to
    // cancel explicitly, and it has to do so BEFORE the re-post or the OS ring
    // and the in-app tone overlap for the length of the round-trip.
    it('cancels the loud notification BEFORE re-posting it silent (handover, not update)', async () => {
      const order: string[] = [];
      mockCancelNotification.mockImplementationOnce(async () => { order.push('cancel'); });
      mockDisplayNotification.mockImplementationOnce(async () => { order.push('display'); });

      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification(BASE_OFFER, { silent: true });

      expect(order).toEqual(['cancel', 'display']);
      expect(mockCancelNotification).toHaveBeenCalledWith('ride-offer-current');
    });

    it('does NOT cancel for a first loud delivery — that would drop the offer card', async () => {
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification(BASE_OFFER);
      expect(mockCancelNotification).not.toHaveBeenCalled();
    });

    // A muted driver opted out of noise, not out of seeing offers: there is no
    // ring to hand over, so cancelling would only make their card blink.
    it('does NOT cancel when merely muted (not silent)', async () => {
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification(BASE_OFFER, { muted: true });
      expect(mockCancelNotification).not.toHaveBeenCalled();
    });

    it('still posts when the cancel rejects — a broken handover must not cost the card', async () => {
      mockCancelNotification.mockRejectedValueOnce(new Error('nothing to cancel'));
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification(BASE_OFFER, { silent: true });
      expect(mockDisplayNotification).toHaveBeenCalledTimes(1);
    });

    // reclaim = app went to background mid-offer. expo-audio pauses the in-app
    // player on that transition, so the OS has to take the ring back or the
    // driver hears nothing for the rest of the window.
    it('reclaim posts LOUD with no full-screen intent, after cancelling', async () => {
      const order: string[] = [];
      mockCancelNotification.mockImplementationOnce(async () => { order.push('cancel'); });
      mockDisplayNotification.mockImplementationOnce(async () => { order.push('display'); });

      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification(BASE_OFFER, { reclaim: true });

      expect(order).toEqual(['cancel', 'display']);
      const req = mockDisplayNotification.mock.calls[0][0];
      expect(req.android.channelId).toBe('ride-offers-v3');
      expect(req.android.loopSound).toBe(true);
      expect(req.android.fullScreenAction).toBeUndefined();
    });

    // A handover re-posts the same id mid-offer. Recomputing a RELATIVE timeout
    // would hand the card a fresh countdown on every handover, so it could
    // outlive the backend's offer and keep showing stale Accept/Decline.
    it('does not extend the dismiss deadline across a handover re-post', async () => {
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      const offer = { ...BASE_OFFER, countdown_seconds: 15 };

      await displayRideOfferNotification(offer);
      expect(mockDisplayNotification.mock.calls[0][0].android.timeoutAfter).toBe(15_000);

      jest.advanceTimersByTime(10_000);
      await displayRideOfferNotification(offer, { silent: true });

      const reposted = mockDisplayNotification.mock.calls[1][0].android.timeoutAfter;
      expect(reposted).toBeLessThanOrEqual(5_000);
    });

    // ...but the pinned deadline must NOT outlive the offer. push_retry.py
    // re-sends a dispatch push for the same ride_id, and an already-elapsed
    // deadline would resolve a timeout of 0 and dismiss the retry before it
    // rendered — the driver would never see the re-offer.
    it('clears the pinned deadline on auto-dismiss so a same-ride re-offer still renders', async () => {
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      const offer = { ...BASE_OFFER, countdown_seconds: 15 };

      await displayRideOfferNotification(offer);
      // Fire the auto-dismiss timer, which is what ends a real ignored offer.
      jest.advanceTimersByTime(15_001);
      mockDisplayNotification.mockClear();

      await displayRideOfferNotification(offer);

      expect(mockDisplayNotification).toHaveBeenCalledTimes(1);
      expect(mockDisplayNotification.mock.calls[0][0].android.timeoutAfter).toBe(15_000);
    });

    // The handover cancels before it posts, so an abort between the two is the
    // one way this code can produce SILENCE rather than noise — and for a
    // `reclaim` the in-app tone is already stopped, so the post is the driver's
    // only remaining alert. Channels persist on the device, so posting anyway is
    // strictly better than giving up.
    it('still posts when channel setup fails — the cancel already happened', async () => {
      mockCreateChannel.mockRejectedValueOnce(new Error('native channel failure'));
      const { displayRideOfferNotification } = require('../../services/notifeeService');

      await expect(
        displayRideOfferNotification(BASE_OFFER, { reclaim: true }),
      ).resolves.toBeUndefined();

      expect(mockCancelNotification).toHaveBeenCalledWith('ride-offer-current');
      expect(mockDisplayNotification).toHaveBeenCalledTimes(1);
    });

    it('mutes sound but keeps the full-screen wake when muted (not silent)', async () => {
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification(BASE_OFFER, { muted: true });

      const req = mockDisplayNotification.mock.calls[0][0];
      expect(req.android.channelId).toBe('ride-offers-fg-v2');
      expect(req.android.fullScreenAction).toBeDefined();
      expect(req.android.sound).toBeUndefined();
    });

    it('immediately dismisses (no display call) when the offer has already expired', async () => {
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification({
        ...BASE_OFFER,
        offer_expires_at: new Date(Date.now() - 60_000).toISOString(),
      });

      expect(mockDisplayNotification).not.toHaveBeenCalled();
      expect(mockCancelNotification).toHaveBeenCalledWith('ride-offer-current');
    });

    it('falls back to a basic notification when the rich render throws', async () => {
      mockDisplayNotification.mockRejectedValueOnce(new Error('unresolved resource'));
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification(BASE_OFFER);

      // First (rich) call rejected, second (basic fallback) call still fires.
      expect(mockDisplayNotification).toHaveBeenCalledTimes(2);
      const fallbackReq = mockDisplayNotification.mock.calls[1][0];
      expect(fallbackReq.id).toBe('ride-offer-current');
      expect(fallbackReq.android.actions).toEqual([
        expect.objectContaining({ pressAction: expect.objectContaining({ id: 'accept' }) }),
        expect.objectContaining({ pressAction: expect.objectContaining({ id: 'decline' }) }),
      ]);
    });

    it('does not throw when both the rich and fallback renders fail', async () => {
      mockDisplayNotification.mockRejectedValue(new Error('always fails'));
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await expect(displayRideOfferNotification(BASE_OFFER)).resolves.toBeUndefined();
      expect(mockDisplayNotification).toHaveBeenCalledTimes(2);
    });

    it('uses the BigPicture style when the offer carries a card image URL', async () => {
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification({
        ...BASE_OFFER,
        offer_card_url: 'https://cdn.spinr.ca/card.png',
      });
      const req = mockDisplayNotification.mock.calls[0][0];
      expect(req.android.style.type).toBe(0); // BIG_PICTURE
      expect(req.android.style.picture).toBe('https://cdn.spinr.ca/card.png');
    });

    it('falls back to BigText style when there is no card image', async () => {
      const { displayRideOfferNotification } = require('../../services/notifeeService');
      await displayRideOfferNotification(BASE_OFFER);
      const req = mockDisplayNotification.mock.calls[0][0];
      expect(req.android.style.type).toBe(1); // BIG_TEXT
    });
  });

  describe('dismissRideOfferNotification', () => {
    it('cancels the fixed notification id', async () => {
      const { dismissRideOfferNotification } = require('../../services/notifeeService');
      await dismissRideOfferNotification();
      expect(mockCancelNotification).toHaveBeenCalledWith('ride-offer-current');
    });

    it('swallows a cancel failure (nothing to dismiss is fine)', async () => {
      mockCancelNotification.mockRejectedValueOnce(new Error('not found'));
      const { dismissRideOfferNotification } = require('../../services/notifeeService');
      await expect(dismissRideOfferNotification()).resolves.toBeUndefined();
    });
  });

  describe('parseRideOfferEvent', () => {
    it('maps an accept action press to {action: "accept"}', () => {
      const { parseRideOfferEvent } = require('../../services/notifeeService');
      const result = parseRideOfferEvent({
        type: EventType.ACTION_PRESS,
        detail: {
          pressAction: { id: 'accept' },
          notification: { data: { type: 'new_ride_assignment', ride_id: 'ride-5' } },
        },
      } as any);
      expect(result).toEqual({ action: 'accept', ride_id: 'ride-5' });
    });

    it('maps a decline action press to {action: "decline"}', () => {
      const { parseRideOfferEvent } = require('../../services/notifeeService');
      const result = parseRideOfferEvent({
        type: EventType.ACTION_PRESS,
        detail: {
          pressAction: { id: 'decline' },
          notification: { data: { type: 'new_ride_assignment', ride_id: 'ride-5' } },
        },
      } as any);
      expect(result).toEqual({ action: 'decline', ride_id: 'ride-5' });
    });

    it('maps a bare notification tap to {action: "tap"}', () => {
      const { parseRideOfferEvent } = require('../../services/notifeeService');
      const result = parseRideOfferEvent({
        type: EventType.PRESS,
        detail: { notification: { data: { type: 'new_ride_assignment', ride_id: 'ride-5' } } },
      } as any);
      expect(result).toEqual({ action: 'tap', ride_id: 'ride-5' });
    });

    it('returns null for an event on a notification of a different type', () => {
      const { parseRideOfferEvent } = require('../../services/notifeeService');
      const result = parseRideOfferEvent({
        type: EventType.PRESS,
        detail: { notification: { data: { type: 'chat_message' } } },
      } as any);
      expect(result).toBeNull();
    });

    it('returns null for an unrecognised event type on our own notification', () => {
      const { parseRideOfferEvent } = require('../../services/notifeeService');
      const result = parseRideOfferEvent({
        type: 99,
        detail: { notification: { data: { type: 'new_ride_assignment' } } },
      } as any);
      expect(result).toBeNull();
    });

    it('handles a missing data payload without throwing', () => {
      const { parseRideOfferEvent } = require('../../services/notifeeService');
      const result = parseRideOfferEvent({ type: EventType.PRESS, detail: {} } as any);
      expect(result).toBeNull();
    });
  });
});
