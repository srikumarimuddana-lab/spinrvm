import spinrConfig from '@shared/config/spinr.config';
import { getAppCheckToken, initFirebaseServices } from '@shared/services/firebase';
import { recordNonFatal } from './crashlytics';
import * as SecureStore from 'expo-secure-store';

const STORAGE_KEY = 'spinr_stationary_tracking_enabled';
let cached = false;
let hydrated = false;
let expiresAt = 0;
let pending: Promise<boolean> | null = null;

/** Default-off until a confirmed setting is available, persisted for headless starts.
 * Refresh on native option application (at most once/minute). Android may defer
 * native reconfiguration until foreground; this is not an instant kill switch.
 * A transient read failure retains the last confirmed value.
 */
export function stationaryTrackingEnabled(): Promise<boolean> {
  if (Date.now() < expiresAt) return Promise.resolve(cached);
  if (pending) return pending;
  pending = readFlag().finally(() => { pending = null; });
  return pending;
}

async function readFlag(): Promise<boolean> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const settings = async () => {
      if (!hydrated) {
        try {
          const stored = await SecureStore.getItemAsync(STORAGE_KEY);
          if (controller.signal.aborted) throw new Error('Tracking settings timed out');
          if (stored === 'true' || stored === 'false') cached = stored === 'true';
          hydrated = true;
        } catch (error) {
          recordNonFatal(error, { domain: 'drivers', surface: 'driver-app', location: 'stationary_flag_restore_failed' });
        }
      }
      if (controller.signal.aborted) throw new Error('Tracking settings timed out');
      await initFirebaseServices();
      const appCheck = await getAppCheckToken();
      if (controller.signal.aborted) throw new Error('Tracking settings timed out');
      const response = await fetch(`${spinrConfig.backendUrl}/api/v1/settings`, {
        signal: controller.signal,
        headers: appCheck ? { 'X-Firebase-AppCheck': appCheck } : {},
      });
      if (!response.ok) throw new Error(`Tracking settings HTTP ${response.status}`);
      const body = await response.json();
      return body?.driver_stationary_tracking_enabled === true;
    };
    cached = await Promise.race([
      settings(),
      new Promise<boolean>((_resolve, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new Error('Tracking settings timed out'));
        }, 3_000);
      }),
    ]);
    // This is a global rollout boolean, not account data. Keep it readable when
    // iOS locks so a cold background runtime does not demote a healthy watcher.
    void SecureStore.setItemAsync(STORAGE_KEY, String(cached), {
      keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
    }).catch(error => recordNonFatal(error, {
      domain: 'drivers', surface: 'driver-app', location: 'stationary_flag_persist_failed',
    }));
  } catch (error) {
    // A failed refresh is not an operator disable. Turning this off here made
    // the minute self-heal replace stationary GPS with a 10m movement filter,
    // after which a parked driver could never send another presence renewal.
    // Keep the last confirmed value; a successful false response still disables.
    recordNonFatal(error, { domain: 'drivers', surface: 'driver-app', location: 'stationary_flag_read_failed' });
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    expiresAt = Date.now() + 60_000;
  }
  return cached;
}
