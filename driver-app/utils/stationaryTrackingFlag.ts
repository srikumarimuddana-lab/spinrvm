import spinrConfig from '@shared/config/spinr.config';
import { getAppCheckToken, initFirebaseServices } from '@shared/services/firebase';
import { recordNonFatal } from './crashlytics';

let cached = false;
let expiresAt = 0;
let pending: Promise<boolean> | null = null;

/** Default-off rollout. No persisted enablement survives a cold headless start.
 * Refresh on native option application (at most once/minute). Android may defer
 * native reconfiguration until foreground; this is not an instant kill switch.
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
  } catch (error) {
    // Optional rollout settings failure must be visible, but cannot prevent
    // existing baseline tracking. Never retain expired experimental enablement.
    cached = false;
    recordNonFatal(error, { domain: 'drivers', surface: 'driver-app', location: 'stationary_flag_read_failed' });
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    expiresAt = Date.now() + 60_000;
  }
  return cached;
}
