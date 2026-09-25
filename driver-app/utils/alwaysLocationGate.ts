/**
 * Android "Allow all the time" gate for going and staying online.
 *
 * Lives outside useDriverDashboard so these decisions can be tested without
 * mounting the dashboard hook, which has no unit harness.
 */
import { Linking } from 'react-native';
import * as Location from 'expo-location';
import { showAlert, useAlertStore } from '../components/AlertDialog';
import { requireAlwaysLocationPermission } from './backgroundLocation';
import { captureException } from '@shared/services/errorReporting';

// Google Play's prominent-disclosure rule for background location: in the app,
// before the system request, naming what is collected, why, and that it runs
// when the app is closed. Continuing needs an explicit tap.
export const BACKGROUND_LOCATION_DISCLOSURE =
  'Spinr Driver collects location data to send you nearby ride offers, guide you to pickups, and share your live position with riders during trips, even when the app is closed or not in use.';

function openSettings(): void {
  Linking.openSettings().catch(() => undefined);
}

/**
 * Resolves true only when the driver taps Continue. The Android back button
 * closes the dialog without calling any button, and another alert can replace
 * it; both resolve false so go-online never hangs on an unanswered dialog.
 */
export function confirmBackgroundLocationDisclosure(): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    let unsubscribe = () => {};
    const finish = (ok: boolean) => {
      if (settled) return;
      settled = true;
      unsubscribe();
      resolve(ok);
    };
    showAlert('Location in the background', BACKGROUND_LOCATION_DISCLOSURE, [
      { text: 'Continue', onPress: () => finish(true) },
      { text: 'Not now', style: 'cancel', onPress: () => finish(false) },
    ]);
    // A button tap hides the dialog before its onPress runs, so wait a tick
    // before treating a hide as a dismissal.
    unsubscribe = useAlertStore.subscribe((state) => {
      if (!state.visible || state.message !== BACKGROUND_LOCATION_DISCLOSURE) {
        setTimeout(() => finish(false), 0);
      }
    });
  });
}

/**
 * Android go-online. Shows the disclosure when Allow all the time is not yet
 * granted, then asks for foreground and background location. Returns true only
 * when both are granted.
 */
export async function ensureAlwaysLocationForGoOnline(): Promise<boolean> {
  const current = await Location.getBackgroundPermissionsAsync();
  if (current.status !== 'granted' && !(await confirmBackgroundLocationDisclosure())) {
    return false;
  }
  if (await requireAlwaysLocationPermission()) return true;
  showAlert(
    'Allow all the time',
    'To go online, set location to Allow all the time. While using the app is not enough — you will not receive ride offers when Spinr is closed.',
    [
      { text: 'Open settings', onPress: openSettings },
      { text: 'Cancel', style: 'cancel' },
    ],
  );
  return false;
}

/**
 * Takes an idle driver offline after they reopen the app without Allow all the
 * time. The backend write comes first: if it fails the driver is still online
 * there, so the app keeps showing online and the caller retries on the next
 * resume. Returns whether the driver went offline.
 *
 * `isCurrent` is re-checked after the write. An offer accepted or a toggle tap
 * during the request makes this decision stale, so local state is left to that
 * newer action. (The backend itself refuses offline during an active trip or
 * offer with 409 OBLIGATION_ACTIVE, which lands in the catch below.)
 */
export async function goOfflineWithoutAlwaysLocation(deps: {
  updateDriverStatus: (online: boolean) => Promise<unknown>;
  setIsOnline: (online: boolean) => void;
  stopTracking: () => Promise<void>;
  isCurrent: () => boolean;
}): Promise<boolean> {
  try {
    await deps.updateDriverStatus(false);
  } catch (error) {
    captureException(
      error instanceof Error ? error : new Error('Offline without always-location failed'),
      { domain: 'drivers', location: 'resume_always_location_offline' },
    );
    return false;
  }
  if (!deps.isCurrent()) return false;
  deps.setIsOnline(false);
  showAlert(
    'Allow all the time',
    'Location must be set to Allow all the time to stay online. While using the app is not enough — ride offers stop when Spinr is closed.',
    [
      { text: 'Open settings', onPress: openSettings },
      { text: 'Not now', style: 'cancel' },
    ],
  );
  try {
    await deps.stopTracking();
  } catch (error) {
    captureException(
      error instanceof Error ? error : new Error('Stop tracking after forced offline failed'),
      { domain: 'drivers', location: 'resume_always_location_stop_tracking' },
    );
  }
  return true;
}
