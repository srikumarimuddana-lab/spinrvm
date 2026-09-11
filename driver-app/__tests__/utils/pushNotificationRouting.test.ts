/**
 * utils/pushNotificationRouting.ts — the shared tap-routing decision used by
 * every push-notification-tap listener in app/_layout.tsx (expo-notifications'
 * and Firebase's). See ACTION_ITEMS.md C97 rec #6 for why both sources exist.
 */
import { routePushNotificationTap } from '../../utils/pushNotificationRouting';

describe('routePushNotificationTap', () => {
  const push = jest.fn();
  const router = { push };

  beforeEach(() => {
    push.mockClear();
  });

  it('routes new_ride_assignment to driver home', () => {
    routePushNotificationTap(router, { type: 'new_ride_assignment' });
    expect(push).toHaveBeenCalledWith('/driver/');
  });

  it('routes chat_message with a ride_id to the chat screen', () => {
    routePushNotificationTap(router, { type: 'chat_message', ride_id: 'ride-123' });
    expect(push).toHaveBeenCalledWith('/driver/chat?rideId=ride-123');
  });

  it('falls back to notifications for chat_message with no ride_id', () => {
    routePushNotificationTap(router, { type: 'chat_message' });
    expect(push).toHaveBeenCalledWith('/driver/notifications');
  });

  it('routes lost_and_found with a case_id to the lost-and-found chat', () => {
    routePushNotificationTap(router, { type: 'lost_and_found', case_id: 'case-9' });
    expect(push).toHaveBeenCalledWith({
      pathname: '/driver/lost-and-found-chat',
      params: { caseId: 'case-9' },
    });
  });

  it('routes lost_and_found_message the same as lost_and_found', () => {
    routePushNotificationTap(router, { type: 'lost_and_found_message', case_id: 'case-9' });
    expect(push).toHaveBeenCalledWith({
      pathname: '/driver/lost-and-found-chat',
      params: { caseId: 'case-9' },
    });
  });

  it('falls back to notifications for lost_and_found with no case_id', () => {
    routePushNotificationTap(router, { type: 'lost_and_found' });
    expect(push).toHaveBeenCalledWith('/driver/notifications');
  });

  it('routes license_backfill_prompt to the profile tab', () => {
    routePushNotificationTap(router, { type: 'license_backfill_prompt' });
    expect(push).toHaveBeenCalledWith('/driver/profile');
  });

  it('falls back to notifications for an unrecognized type', () => {
    routePushNotificationTap(router, { type: 'document_expiry_warning' });
    expect(push).toHaveBeenCalledWith('/driver/notifications');
  });

  it('falls back to notifications for empty/no data (e.g. the local welcome nudge)', () => {
    routePushNotificationTap(router, {});
    expect(push).toHaveBeenCalledWith('/driver/notifications');
  });
});
