/**
 * rideVoltraLiveActivity — iOS Live Activity lifecycle via Voltra. The iOS
 * counterpart of services/rideLiveNotification.ts (Android notifee): start the
 * activity at driver_accepted, end it on a terminal ride state. Voltra renders
 * the UI on-device (components/VoltraRideActivity.tsx) and surfaces the
 * ActivityKit push token; the backend pushes updates via direct APNs while the
 * app is backgrounded/killed (backend/utils/apns_client.py).
 *
 * iOS-only; Voltra is lazy-required so Android / Expo Go / web / an un-built iOS
 * client import this harmlessly (every export no-ops).
 *
 * ⚠️ BUILD-TIME VERIFY: the exact Voltra v2 export names used below
 * (startLiveActivity / endLiveActivity / addVoltraListener, the
 * 'activityTokenReceived' event + its payload key) are from the Phase 3 research
 * synthesis and MUST be confirmed against the INSTALLED
 * node_modules/@use-voltra/ios-client types before the EAS build — they were not
 * fully documented. The shape here (lazy-require guard, start/end/onToken→register)
 * is the stable contract; only the Voltra call names may need adjusting.
 */
import { Platform } from 'react-native';

let voltra: any = null;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    voltra = require('@use-voltra/ios-client');
} catch {
    /* native module unavailable (Android / Expo Go / web / un-built iOS) */
}

const available = () => Boolean(voltra) && Platform.OS === 'ios';

export interface VoltraContent {
    status: string;
    headline: string;
    etaMinutes?: number | null;
    driverName?: string | null;
    vehicleLabel?: string | null;
    pickupArea?: string | null;
    dropoffArea?: string | null;
    rideCode?: string | null;
}

// One activity per ride session; the backend's APNs topic + the on-device UI
// key off this name. Must match the generator (render-voltra-templates.mjs).
const ACTIVITY_NAME = 'spinr-ride';
export const getActivityName = () => ACTIVITY_NAME;

/** Start (or restart) the Live Activity with the initial content. */
export async function startActivity(content: VoltraContent): Promise<void> {
    if (!available()) return;
    try {
        await voltra.startLiveActivity?.({
            activityName: ACTIVITY_NAME,
            attributes: { name: ACTIVITY_NAME },
            contentState: content,
        });
    } catch (e) {
        console.warn('[Voltra] startActivity failed:', e);
    }
}

/** End the Live Activity (ride completed / cancelled). */
export async function endActivity(): Promise<void> {
    if (!available()) return;
    try {
        await voltra.endLiveActivity?.({ activityName: ACTIVITY_NAME });
    } catch (e) {
        console.warn('[Voltra] endActivity failed:', e);
    }
}

/**
 * Subscribe to the ActivityKit push-update token (fires after start, and again
 * on rotation). Returns an unsubscribe function. The caller registers the token
 * with the backend so APNs updates reach the activity even when the app is
 * suspended/killed.
 */
export function onTokenReceived(cb: (token: string) => void): () => void {
    if (!available()) return () => {};
    try {
        const sub = voltra.addVoltraListener?.('activityTokenReceived', (e: any) => {
            const token = e?.pushToken ?? e?.token;
            if (token) cb(String(token));
        });
        return () => sub?.remove?.();
    } catch {
        return () => {};
    }
}
