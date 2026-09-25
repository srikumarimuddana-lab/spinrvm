/* eslint-disable import/first */
/**
 * Android "Allow all the time" gate used by useDriverDashboard: the Play
 * prominent disclosure before the permission request, and the forced-offline
 * path when an idle driver reopens the app without the grant.
 */

let mockBgPermission = 'denied';
const mockRequireAlways = jest.fn(async () => true);
const mockCaptureException = jest.fn();
const mockOpenSettings = jest.fn(() => Promise.resolve());

jest.mock('expo-location', () => ({
  getBackgroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: mockBgPermission })),
}));

jest.mock('../backgroundLocation', () => ({
  requireAlwaysLocationPermission: () => mockRequireAlways(),
}));

jest.mock('@shared/services/errorReporting', () => ({
  captureException: (...args: unknown[]) => mockCaptureException(...args),
}));

// A real store, so the dialog's hide-then-onPress order is exercised.
jest.mock('../../components/AlertDialog', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { create } = require('zustand');
  const useAlertStore = create(() => ({ visible: false, title: '', message: '', buttons: [] }));
  return {
    useAlertStore,
    showAlert: jest.fn((title: string, message: string, buttons: unknown[]) =>
      useAlertStore.setState({ visible: true, title, message, buttons })),
    hideAlert: () => useAlertStore.setState({ visible: false }),
  };
});

import { Linking } from 'react-native';
import { showAlert, hideAlert, useAlertStore } from '../../components/AlertDialog';
import {
  BACKGROUND_LOCATION_DISCLOSURE,
  confirmBackgroundLocationDisclosure,
  ensureAlwaysLocationForGoOnline,
  goOfflineWithoutAlwaysLocation,
} from '../alwaysLocationGate';

type Button = { text: string; onPress?: () => void };

// What AlertDialog.handlePress does: hide first, then the button's onPress.
function press(text: string) {
  const buttons = (useAlertStore.getState() as { buttons: Button[] }).buttons;
  const button = buttons.find((b) => b.text === text);
  if (!button) throw new Error(`no ${text} button`);
  hideAlert();
  button.onPress?.();
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

// Real react-native, only openSettings stubbed. A hand-written react-native
// mock crashed the suite: jest-expo's lazy globals load expo's own runtime,
// which needs the real Platform and StyleSheet.
jest.spyOn(Linking, 'openSettings').mockImplementation(() => mockOpenSettings());

// Expo installs `fetch` as a lazy global that loads its implementation on first
// read. Nothing in this suite reads it, so the first read would be Jest 30's
// post-suite globals cleanup, which runs outside test scope and fails the whole
// suite ("import a file outside of the scope of the test code"). jest.setup.js
// pre-reads the other Expo lazy globals but not fetch, so give it a plain value.
Object.defineProperty(globalThis, 'fetch', { value: jest.fn(), configurable: true, writable: true });

beforeEach(() => {
  jest.clearAllMocks();
  mockBgPermission = 'denied';
  mockRequireAlways.mockResolvedValue(true);
  useAlertStore.setState({ visible: false, title: '', message: '', buttons: [] });
});

describe('confirmBackgroundLocationDisclosure', () => {
  it('resolves true on Continue even though the dialog hides first', async () => {
    const result = confirmBackgroundLocationDisclosure();
    press('Continue');
    await expect(result).resolves.toBe(true);
  });

  it('resolves false on Not now', async () => {
    const result = confirmBackgroundLocationDisclosure();
    press('Not now');
    await expect(result).resolves.toBe(false);
  });

  it('resolves false when the back button closes it without a tap', async () => {
    const result = confirmBackgroundLocationDisclosure();
    hideAlert();
    await expect(result).resolves.toBe(false);
  });

  it('resolves false when another alert replaces it', async () => {
    const result = confirmBackgroundLocationDisclosure();
    useAlertStore.setState({ visible: true, message: 'something else' });
    await expect(result).resolves.toBe(false);
  });
});

describe('ensureAlwaysLocationForGoOnline', () => {
  it('shows the disclosure before the permission request', async () => {
    const result = ensureAlwaysLocationForGoOnline();
    await flush();
    expect(showAlert).toHaveBeenCalledWith(
      expect.any(String), BACKGROUND_LOCATION_DISCLOSURE, expect.any(Array),
    );
    expect(mockRequireAlways).not.toHaveBeenCalled();
    press('Continue');
    await expect(result).resolves.toBe(true);
    expect(mockRequireAlways).toHaveBeenCalledTimes(1);
  });

  it('does not request the permission when the driver declines the disclosure', async () => {
    const result = ensureAlwaysLocationForGoOnline();
    await flush();
    press('Not now');
    await expect(result).resolves.toBe(false);
    expect(mockRequireAlways).not.toHaveBeenCalled();
  });

  it('skips the disclosure when Allow all the time is already granted', async () => {
    mockBgPermission = 'granted';
    await expect(ensureAlwaysLocationForGoOnline()).resolves.toBe(true);
    expect(showAlert).not.toHaveBeenCalled();
  });

  it('offers settings when the driver stops at While using the app', async () => {
    mockRequireAlways.mockResolvedValue(false);
    const result = ensureAlwaysLocationForGoOnline();
    await flush();
    press('Continue');
    await expect(result).resolves.toBe(false);
    expect(showAlert).toHaveBeenLastCalledWith('Allow all the time', expect.any(String), expect.any(Array));
    press('Open settings');
    expect(mockOpenSettings).toHaveBeenCalled();
  });
});

describe('goOfflineWithoutAlwaysLocation', () => {
  function deps() {
    return {
      updateDriverStatus: jest.fn(async () => undefined),
      setIsOnline: jest.fn(),
      stopTracking: jest.fn(async () => undefined),
      isCurrent: jest.fn(() => true),
    };
  }

  it('leaves local state alone when an offer or toggle overtook the request', async () => {
    const d = deps();
    d.isCurrent.mockReturnValue(false);
    await expect(goOfflineWithoutAlwaysLocation(d)).resolves.toBe(false);
    expect(d.setIsOnline).not.toHaveBeenCalled();
    expect(d.stopTracking).not.toHaveBeenCalled();
    expect(showAlert).not.toHaveBeenCalled();
  });

  it('writes offline to the backend, then flips the app and stops tracking', async () => {
    const d = deps();
    await expect(goOfflineWithoutAlwaysLocation(d)).resolves.toBe(true);
    expect(d.updateDriverStatus).toHaveBeenCalledWith(false);
    expect(d.setIsOnline).toHaveBeenCalledWith(false);
    expect(d.stopTracking).toHaveBeenCalledTimes(1);
    expect(showAlert).toHaveBeenCalledWith('Allow all the time', expect.any(String), expect.any(Array));
  });

  it('keeps the app online and reports when the backend write fails', async () => {
    const d = deps();
    d.updateDriverStatus.mockRejectedValue(new Error('503'));
    await expect(goOfflineWithoutAlwaysLocation(d)).resolves.toBe(false);
    expect(d.setIsOnline).not.toHaveBeenCalled();
    expect(d.stopTracking).not.toHaveBeenCalled();
    expect(mockCaptureException).toHaveBeenCalledWith(
      expect.any(Error), expect.objectContaining({ location: 'resume_always_location_offline' }),
    );
  });

  it('reports a teardown failure without undoing the offline flip', async () => {
    const d = deps();
    d.stopTracking.mockRejectedValue(new Error('native'));
    await expect(goOfflineWithoutAlwaysLocation(d)).resolves.toBe(true);
    expect(d.setIsOnline).toHaveBeenCalledWith(false);
    expect(mockCaptureException).toHaveBeenCalledWith(
      expect.any(Error), expect.objectContaining({ location: 'resume_always_location_stop_tracking' }),
    );
  });
});
