const mockRequest = jest.fn();

import { PermissionsAndroid, Platform } from 'react-native';
import { requestAndroidNotificationPermission } from '../../../shared/services/firebase';

describe('Android notification permission', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    Object.defineProperty(Platform, 'OS', { value: 'android', configurable: true });
    Object.defineProperty(Platform, 'Version', { value: 35, configurable: true });
    (PermissionsAndroid as any).request = mockRequest;
  });

  it('requests POST_NOTIFICATIONS on Android 13 and reports a denial', async () => {
    mockRequest.mockResolvedValue('denied');

    await expect(requestAndroidNotificationPermission()).resolves.toBe(false);
    expect(mockRequest).toHaveBeenCalledWith(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
  });

  it('reports Android notification permission as granted only after the OS grants it', async () => {
    mockRequest.mockResolvedValue('granted');

    await expect(requestAndroidNotificationPermission()).resolves.toBe(true);
  });
});
