import * as SecureStore from 'expo-secure-store';
import * as Crypto from 'expo-crypto';
import { isAppCheckRejection } from '../../shared/auth/appCheckRejection';
import { SESSION_ENDED_KEY } from '../../shared/auth/sessionMarker';
import { withSessionLock, sessionKeychainOptions } from '../../shared/auth/sessionLock';
import SpinrConfig from '@shared/config/spinr.config';
import { initFirebaseServices, getAppCheckToken } from '@shared/services/firebase';
import { recordNonFatal } from './crashlytics';

/** No React/auth-store imports: this also runs in a headless JS runtime. */
export function createBackgroundTokenProvider(): () => Promise<string | null> {
  let failedCandidate: string | null = null;
  let retryAfter = 0;
  return async () => {
    try {
      return await withSessionLock(async () => {
        if (await SecureStore.getItemAsync(SESSION_ENDED_KEY)) return null;
        const token = await SecureStore.getItemAsync('fg_access_token');
        const expiry = Number(await SecureStore.getItemAsync('token_expires_at'));
        if (token && expiry > Date.now() + 60_000) return token;
        const candidate = await SecureStore.getItemAsync('refresh_token');
        if (!candidate || !SpinrConfig.backendUrl) return null;
        if (candidate === failedCandidate && Date.now() < retryAfter) return null;
        if (candidate !== failedCandidate) retryAfter = 0;
        const fingerprint = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, candidate);
        if (await SecureStore.getItemAsync('bg_rejected_refresh') === fingerprint) return null;

        const controller = new AbortController();
        // CRIMSON-SMOKE-7445-121: a single 10s deadline shared across all three
        // sequential steps (App Check, fetch, json parse) let one slow step
        // starve the others -- e.g. an 8s App Check left only 2s for the POST,
        // not enough for real network latency. Each step now gets its own
        // independent deadline sized for that step's own realistic worst case,
        // not a slice of the old shared total -- otherwise a step that used to
        // fit inside the shared budget could still starve the next one. Worst
        // case sums to 18s (App Check 8s + POST 8s + json() parsing 2s).
        // Stacked with nativeSessionLock.ts's own independent 10s lock-wait
        // deadline (already acquired by this point), true worst case is
        // ~27-28s against iOS's ~30s background-execution ceiling -- a thin
        // but deliberate margin (this is the residual risk the investigator's
        // un-chosen "start one shared clock before the lock" alternative
        // would have bounded more tightly by never stacking two independent
        // ceilings; the per-step approach was chosen instead per an explicit
        // user decision).
        const withStepDeadline = <T>(promise: Promise<T>, ms: number): Promise<T> => {
          let timeout: ReturnType<typeof setTimeout>;
          const deadline = new Promise<never>((_resolve, reject) => {
            timeout = setTimeout(() => {
              controller.abort();
              reject(new Error('Background authentication deadline exceeded'));
            }, ms);
          });
          return Promise.race([promise, deadline]).finally(() => clearTimeout(timeout));
        };
        try {
          // A hung native App Check promise must release the session lock too.
          // Late preparation may finish, but cannot continue to the POST.
          const appCheck = await withStepDeadline(initFirebaseServices().then(() => getAppCheckToken()), 8_000);
          const response = await withStepDeadline(fetch(`${SpinrConfig.backendUrl}/api/v1/auth/refresh`, {
            method: 'POST', signal: controller.signal, credentials: 'omit',
            headers: { 'Content-Type': 'application/json', ...(appCheck ? { 'X-Firebase-AppCheck': appCheck } : {}) },
            body: JSON.stringify({ refresh_token: candidate }),
          }), 8_000);
          if (!response.ok) {
            // A rejected credential must not be replayed on every GPS callback.
            // Foreground auth owns definitive sign-out and account recovery.
            // An App Check 401 is not that rejection: the refresh token was
            // never checked. Back off 30s (the catch below) and try again.
            let appCheckRejection = false;
            if (response.status === 401 && typeof response.json === 'function') {
              try {
                appCheckRejection = isAppCheckRejection(await withStepDeadline(response.json(), 2_000));
              } catch {
                appCheckRejection = false;
              }
            }
            if (response.status === 401 && !appCheckRejection) {
              retryAfter = Infinity;
              await SecureStore.setItemAsync('bg_rejected_refresh', fingerprint, sessionKeychainOptions);
            }
            throw new Error(`Background token refresh HTTP ${response.status}`);
          }
          const data = await withStepDeadline(response.json(), 2_000);
          const accessExpiresAtMs = typeof data.access_expires_at === 'string' ? Date.parse(data.access_expires_at) : NaN;
          const serverNowMs = Date.parse(response.headers?.get('date') ?? '');
          let expiresIn = typeof data.expires_in === 'number' && Number.isFinite(data.expires_in) && data.expires_in > 0
            ? data.expires_in
            : null;
          if (expiresIn !== null && !Number.isFinite(Date.now() + expiresIn * 1000)) expiresIn = null;
          if (expiresIn === null && Number.isFinite(accessExpiresAtMs)) {
            expiresIn = Math.max(0, (accessExpiresAtMs - (Number.isFinite(serverNowMs) ? serverNowMs : Date.now())) / 1000);
          }
          if (expiresIn !== null && !Number.isFinite(Date.now() + expiresIn * 1000)) expiresIn = null;
          if (typeof data.token !== 'string' || !data.token || typeof data.refresh_token !== 'string' ||
              !data.refresh_token || expiresIn === null) {
            throw new Error('Background token refresh returned invalid credentials');
          }
          // All current writers use the lock. These fences also protect against
          // an obsolete/uncoordinated task or sign-out during an app upgrade.
          if (await SecureStore.getItemAsync(SESSION_ENDED_KEY) ||
              await SecureStore.getItemAsync('refresh_token') !== candidate) return null;
          const tokenExpiresAt = Date.now() + expiresIn * 1000;
          // Save the successor first: if a later write fails, the next callback
          // can still renew using the credential the server now recognizes.
          await SecureStore.setItemAsync('refresh_token', data.refresh_token, sessionKeychainOptions);
          await SecureStore.setItemAsync('fg_access_token', data.token, sessionKeychainOptions);
          await SecureStore.setItemAsync('token_expires_at', String(tokenExpiresAt), sessionKeychainOptions);
          failedCandidate = null;
          retryAfter = 0;
          return await SecureStore.getItemAsync(SESSION_ENDED_KEY) ? null : data.token;
        } catch (error) {
          failedCandidate = candidate;
          if (retryAfter !== Infinity) retryAfter = Date.now() + 30_000;
          throw error;
        }
      });
    } catch (error) {
      // Never include tokens, response bodies, or coordinates in diagnostics.
      console.error('[BgAuth] Authentication unavailable; recorded positions retained');
      recordNonFatal(error instanceof Error ? error : new Error('Background authentication failed'), {
        domain: 'auth', surface: 'driver-app', reason: 'background_token_refresh',
      });
      return null;
    }
  };
}

export const renewBackgroundAuthToken = createBackgroundTokenProvider();
