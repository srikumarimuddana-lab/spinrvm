/**
 * Meta (Facebook) app events — rider and driver apps.
 *
 * Scope is deliberately narrow. Spinr sends Advanced Matching (hashed email /
 * phone / external_id) from the BACKEND via the Conversions API, not from the
 * device. That decision is what keeps this integration out of App Tracking
 * Transparency territory entirely:
 *
 *   - no IDFA is collected
 *   - `setAdvertiserTrackingEnabled` is pinned to false on iOS
 *   - no user data is attached to app events here
 *   - both apps keep `NSPrivacyTracking: false` in app.config.ts, which stays
 *     truthful, so the App Store privacy label does not change
 *
 * Do NOT add `Settings.setUserData(...)` or flip advertiser tracking on
 * without also updating the privacy manifests, adding an ATT prompt and
 * NSUserTrackingUsageDescription, and re-answering the App Store Connect
 * privacy questionnaire. That is a store-submission change, not a code change.
 *
 * What this module IS for: app lifecycle + install events (so Meta can
 * attribute installs and the App Promotion campaign works at all), and the
 * client half of CompleteRegistration.
 */

// react-native-fbsdk-next is a native module: it exists only in binaries built
// after it was added. The guarded require keeps Expo Go, older installed
// builds, web, and Jest working — they simply log nothing.
let FBSDK: any = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  FBSDK = require('react-native-fbsdk-next');
} catch {
  FBSDK = null;
}

let initialized = false;

export type MetaAppSurface = 'rider' | 'driver';

/**
 * Initialize the SDK. Safe to call more than once; safe to call when the
 * native module or the config is missing.
 *
 * appId/clientToken come from EXPO_PUBLIC_FB_APP_ID / EXPO_PUBLIC_FB_CLIENT_TOKEN.
 * Both are client-side identifiers by design — they ship inside the app binary
 * regardless, and neither can read or write data on their own. The CAPI access
 * token, which CAN, never appears in app code.
 */
export function initMetaSdk(appId?: string, clientToken?: string): boolean {
  if (initialized) return true;
  if (!FBSDK?.Settings) return false;
  if (!appId || !clientToken) {
    // Expected before the Meta App IDs are provisioned. Not an error — the app
    // must run normally without them.
    return false;
  }

  try {
    const { Settings } = FBSDK;

    // Pinned false: Spinr does not do advertiser tracking from the device.
    // Called BEFORE initializeSDK so the very first automatic event (app
    // install / activation) is already emitted under the non-tracking mode
    // rather than briefly running with the SDK default.
    Settings.setAdvertiserTrackingEnabled?.(false);
    Settings.setAdvertiserIDCollectionEnabled?.(false);

    Settings.setAppID?.(appId);
    Settings.setClientToken?.(clientToken);

    // Automatic app-lifecycle events (install, activation, session). These are
    // what make install→registration attribution possible, and they carry no
    // user data.
    Settings.setAutoLogAppEventsEnabled?.(true);

    Settings.initializeSDK?.();
    initialized = true;
    return true;
  } catch {
    // Never let analytics initialization break app startup.
    return false;
  }
}

/**
 * Fire the client-side CompleteRegistration.
 *
 * `eventId` MUST be the value the backend returned on the auth response
 * (`meta_event_id`). The backend sends its own copy of this event with the
 * same id, and Meta collapses the pair on (event_name, event_id). Passing a
 * locally-generated id here — or omitting it — makes Meta count the signup
 * twice, which is worse than not firing at all.
 *
 * Called only on a genuine new-account response, never on a returning login.
 */
export function logCompleteRegistration(params: {
  eventId?: string | null;
  surface: MetaAppSurface;
  registrationMethod?: string;
}): void {
  const { eventId, surface, registrationMethod = 'phone' } = params;

  // No shared id means the server copy cannot be de-duplicated against this
  // one. Skipping is the correct trade: the server event still lands with full
  // Advanced Matching, so the conversion is counted once, accurately.
  if (!eventId) return;
  if (!FBSDK?.AppEventsLogger || !initialized) return;

  // The SDK's STANDARD completed-registration event
  // (`fb_mobile_complete_registration`), never the literal string
  // 'CompleteRegistration'. logEvent treats an arbitrary literal as a CUSTOM
  // app event, which would not resolve to the same standard event as the
  // backend's CAPI `CompleteRegistration` — breaking both registration
  // optimisation and the cross-source de-duplication this event exists for.
  const eventName: string = FBSDK.AppEventsLogger.AppEvents?.CompletedRegistration ?? 'fb_mobile_complete_registration';

  try {
    FBSDK.AppEventsLogger.logEvent(eventName, {
      // Meta's de-duplication key for app events. Verify in Events Manager →
      // Test Events before going live: if the two copies show as two
      // conversions rather than one, this key is what to check first.
      event_id: eventId,
      // Mirrors the server-side custom_data so reporting reads the same on
      // both copies.
      content_category: surface === 'driver' ? 'driver_application' : 'rider',
      registration_method: registrationMethod,
    });
  } catch {
    // Analytics must never break the signup flow.
  }
}

/** Test seam — resets module state between tests. */
export function __resetMetaSdkForTests(): void {
  initialized = false;
}

export default { initMetaSdk, logCompleteRegistration };
