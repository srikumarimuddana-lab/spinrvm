/**
 * P3-19: Native push-notification flows (FCM/APNs) — rider side
 *
 * Pins useScheduledRideReminder hook:
 *   - scheduleReminder: skips when < 10 min away (matching the backend's
 *     scheduled_ride_reminder lead time); schedules otherwise; cancels old
 *     reminder before re-scheduling (idempotent)
 *   - cancelReminder: cancels notification and clears storage
 *   - handleScheduledRideReminderFCM: extracts rideId from FCM payload;
 *     returns null for wrong type
 *
 * Code under test:
 *   rider-app/hooks/useScheduledRideReminder.ts::scheduleReminder
 *   rider-app/hooks/useScheduledRideReminder.ts::cancelReminder
 *   rider-app/hooks/useScheduledRideReminder.ts::handleScheduledRideReminderFCM
 *
 * Note: expo-notifications calls are mocked — native delivery is tested
 * manually via docs/MOBILE_SMOKE.md §E (see P3-19 xfail in test_p3_push_notifications.py).
 */

import {
  useScheduledRideReminder,
  handleScheduledRideReminderFCM,
} from '../useScheduledRideReminder';
import AsyncStorage from '@react-native-async-storage/async-storage';

jest.mock('react', () => ({
  ...jest.requireActual('react'),
  useCallback: (fn: any) => fn,
}));

jest.mock('react-native', () => ({
  Platform: { OS: 'ios' },
}));

jest.mock('@react-native-async-storage/async-storage', () => {
  const _store: Record<string, string> = {};
  return {
    getItem: jest.fn((k: string) => Promise.resolve(_store[k] ?? null)),
    setItem: jest.fn((k: string, v: string) => { _store[k] = v; return Promise.resolve(); }),
    removeItem: jest.fn((k: string) => { delete _store[k]; return Promise.resolve(); }),
    _store,
    _reset: () => { Object.keys(_store).forEach((k) => delete _store[k]); },
  };
});

jest.mock('expo-notifications', () => ({
  scheduleNotificationAsync: jest.fn(),
  cancelScheduledNotificationAsync: jest.fn(),
}), { virtual: true });

jest.mock('../../../config', () => ({
  API_URL: 'http://localhost:8000',
}), { virtual: true });

 
const ExpoNotifications = require('expo-notifications') as Record<string, jest.Mock>;
const mockScheduleNotification = ExpoNotifications.scheduleNotificationAsync;
const mockCancelNotification = ExpoNotifications.cancelScheduledNotificationAsync;

const REMINDER_MAP_KEY = '@spinr:scheduled_reminders';
const RIDE_ID = 'ride-sch-001';

beforeEach(() => {
  jest.clearAllMocks();
  (AsyncStorage as any)._reset();
});

describe('useScheduledRideReminder — scheduleReminder', () => {
  it('schedules a notification when the ride is > 10 min away', async () => {
    mockScheduleNotification.mockResolvedValue('notif-id-001');

    const { scheduleReminder } = useScheduledRideReminder();
    const futureTime = new Date(Date.now() + 60 * 60 * 1000); // 1 hour away

    await scheduleReminder(RIDE_ID, futureTime);

    expect(mockScheduleNotification).toHaveBeenCalledTimes(1);
    const [config] = mockScheduleNotification.mock.calls[0];
    expect(config.content.data.rideId).toBe(RIDE_ID);
    expect(config.content.data.type).toBe('scheduled_ride_reminder');
  });

  it('skips scheduling when the ride is < 10 min away', async () => {
    const { scheduleReminder } = useScheduledRideReminder();
    const soonTime = new Date(Date.now() + 5 * 60 * 1000); // 5 min away

    await scheduleReminder(RIDE_ID, soonTime);

    expect(mockScheduleNotification).not.toHaveBeenCalled();
  });

  it('schedules when the ride is 12 min away — regression guard for the old 15-min lead', async () => {
    // Previously this lead time was 15 minutes (mismatched against the
    // backend's 10-minute FCM reminder); a ride 12 minutes away used to be
    // silently skipped client-side even though the backend reminder hadn't
    // fired yet either. Now both sides agree on 10 minutes, so 12-out schedules.
    mockScheduleNotification.mockResolvedValue('notif-id-002');

    const { scheduleReminder } = useScheduledRideReminder();
    const twelveMinOut = new Date(Date.now() + 12 * 60 * 1000);

    await scheduleReminder(RIDE_ID, twelveMinOut);

    expect(mockScheduleNotification).toHaveBeenCalledTimes(1);
  });

  it('cancels the old notification before re-scheduling (idempotent)', async () => {
    mockScheduleNotification.mockResolvedValue('notif-id-new');

    // Pre-load an existing notif id for this ride
    await AsyncStorage.setItem(
      REMINDER_MAP_KEY,
      JSON.stringify({ [RIDE_ID]: 'notif-id-old' }),
    );

    const { scheduleReminder } = useScheduledRideReminder();
    const futureTime = new Date(Date.now() + 60 * 60 * 1000);

    await scheduleReminder(RIDE_ID, futureTime);

    expect(mockCancelNotification).toHaveBeenCalledWith('notif-id-old');
    expect(mockScheduleNotification).toHaveBeenCalledTimes(1);
  });

  it('stores the new notification id in AsyncStorage', async () => {
    mockScheduleNotification.mockResolvedValue('notif-id-stored');

    const { scheduleReminder } = useScheduledRideReminder();
    await scheduleReminder(RIDE_ID, new Date(Date.now() + 60 * 60 * 1000));

    const raw = await AsyncStorage.getItem(REMINDER_MAP_KEY);
    const map = JSON.parse(raw!);
    expect(map[RIDE_ID]).toBe('notif-id-stored');
  });
});

describe('useScheduledRideReminder — cancelReminder', () => {
  it('cancels the notification and removes it from storage', async () => {
    await AsyncStorage.setItem(
      REMINDER_MAP_KEY,
      JSON.stringify({ [RIDE_ID]: 'notif-id-to-cancel' }),
    );

    const { cancelReminder } = useScheduledRideReminder();
    await cancelReminder(RIDE_ID);

    expect(mockCancelNotification).toHaveBeenCalledWith('notif-id-to-cancel');

    const raw = await AsyncStorage.getItem(REMINDER_MAP_KEY);
    const map = JSON.parse(raw!);
    expect(map[RIDE_ID]).toBeUndefined();
  });

  it('is a no-op when no reminder exists for the ride', async () => {
    const { cancelReminder } = useScheduledRideReminder();
    await cancelReminder('ride-never-scheduled');

    expect(mockCancelNotification).not.toHaveBeenCalled();
  });
});

describe('handleScheduledRideReminderFCM', () => {
  it('returns rideId from a valid scheduled_ride_reminder message', () => {
    const msg = { data: { type: 'scheduled_ride_reminder', ride_id: 'ride-fcm-001' } };
    expect(handleScheduledRideReminderFCM(msg)).toBe('ride-fcm-001');
  });

  it('also handles rideId (camelCase) in FCM payload', () => {
    const msg = { data: { type: 'scheduled_ride_reminder', rideId: 'ride-fcm-002' } };
    expect(handleScheduledRideReminderFCM(msg)).toBe('ride-fcm-002');
  });

  it('returns null for messages of a different type', () => {
    const msg = { data: { type: 'ride_offer', ride_id: 'ride-003' } };
    expect(handleScheduledRideReminderFCM(msg)).toBeNull();
  });

  it('returns null when data is missing', () => {
    expect(handleScheduledRideReminderFCM(null)).toBeNull();
    expect(handleScheduledRideReminderFCM({})).toBeNull();
  });
});
