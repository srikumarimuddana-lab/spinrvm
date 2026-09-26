/**
 * P3-19: Native push-notification flows (FCM/APNs) — rider side
 *
 * Pins server-owned reminders and cleanup of legacy local notifications.
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
  clearLegacyScheduledReminders,
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
}));

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
  mockCancelNotification.mockReset();
  (AsyncStorage as any)._reset();
});

describe('server-owned reminders', () => {
  it('never schedules a hardcoded local reminder', async () => {
    const { scheduleReminder } = useScheduledRideReminder();
    await scheduleReminder(RIDE_ID, new Date(Date.now() + 60 * 60 * 1000));
    expect(mockScheduleNotification).not.toHaveBeenCalled();
  });
  it('retains failed cancellations while clearing successful ones', async () => {
    await AsyncStorage.setItem(REMINDER_MAP_KEY, JSON.stringify({a:'old-a', b:'old-b'}));
    mockCancelNotification.mockRejectedValueOnce(new Error('native unavailable')).mockResolvedValueOnce(undefined);
    await clearLegacyScheduledReminders();
    expect(mockCancelNotification).toHaveBeenCalledWith('old-a');
    expect(mockCancelNotification).toHaveBeenCalledWith('old-b');
    expect(JSON.parse((await AsyncStorage.getItem(REMINDER_MAP_KEY))!)).toEqual({a:'old-a'});
    expect(mockScheduleNotification).not.toHaveBeenCalled();
  });
  it.each(['null', '[]', '"invalid"', '{"ride":3}'])('ignores invalid storage: %s', async raw => {
    await AsyncStorage.setItem(REMINDER_MAP_KEY, raw);
    await expect(clearLegacyScheduledReminders()).resolves.toBeUndefined();
    expect(mockCancelNotification).not.toHaveBeenCalled();
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
