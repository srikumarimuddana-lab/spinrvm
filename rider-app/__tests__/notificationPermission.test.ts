import { PermissionsAndroid, Platform, Linking } from 'react-native';
import {
  requestNotificationPermission,
  checkNotificationPermission,
  openNotificationSettings,
  requestAndroidNotificationPermission,
} from '@shared/services/firebase';

jest.mock('react-native', () => {
  const RN = jest.requireActual('react-native');
  RN.PermissionsAndroid = {
    request: jest.fn(),
    check: jest.fn(),
    PERMISSIONS: {
      POST_NOTIFICATIONS: 'android.permission.POST_NOTIFICATIONS',
    },
    RESULTS: {
      GRANTED: 'granted',
      DENIED: 'denied',
    },
  };
  RN.Linking = {
    openSettings: jest.fn(() => Promise.resolve()),
    openURL: jest.fn(() => Promise.resolve()),
  };
  return RN;
});

describe('Notification Permission Service Helpers', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('Android Notification Permission', () => {
    it('requests POST_NOTIFICATIONS on Android 13+ and returns false if denied', async () => {
      Object.defineProperty(Platform, 'OS', { value: 'android', configurable: true });
      Object.defineProperty(Platform, 'Version', { value: 33, configurable: true });
      (PermissionsAndroid.request as jest.Mock).mockResolvedValue('denied');

      const result = await requestAndroidNotificationPermission();
      expect(result).toBe(false);
      expect(PermissionsAndroid.request).toHaveBeenCalledWith('android.permission.POST_NOTIFICATIONS');
    });

    it('returns true when OS grants POST_NOTIFICATIONS', async () => {
      Object.defineProperty(Platform, 'OS', { value: 'android', configurable: true });
      Object.defineProperty(Platform, 'Version', { value: 33, configurable: true });
      (PermissionsAndroid.request as jest.Mock).mockResolvedValue('granted');

      const result = await requestAndroidNotificationPermission();
      expect(result).toBe(true);
    });

    it('returns true automatically on Android < 33', async () => {
      Object.defineProperty(Platform, 'OS', { value: 'android', configurable: true });
      Object.defineProperty(Platform, 'Version', { value: 30, configurable: true });

      const result = await requestAndroidNotificationPermission();
      expect(result).toBe(true);
      expect(PermissionsAndroid.request).not.toHaveBeenCalled();
    });
  });

  describe('checkNotificationPermission', () => {
    it('returns granted status when permission is active', async () => {
      Object.defineProperty(Platform, 'OS', { value: 'ios', configurable: true });
      const status = await checkNotificationPermission();
      expect(status.granted).toBe(true);
    });
  });

  describe('openNotificationSettings', () => {
    it('calls Linking.openSettings on Android', async () => {
      Object.defineProperty(Platform, 'OS', { value: 'android', configurable: true });
      await openNotificationSettings();
      expect(Linking.openSettings).toHaveBeenCalled();
    });

    it('calls Linking.openURL with app-settings on iOS', async () => {
      Object.defineProperty(Platform, 'OS', { value: 'ios', configurable: true });
      await openNotificationSettings();
      expect(Linking.openURL).toHaveBeenCalledWith('app-settings:');
    });
  });
});
