/**
 * rideLiveNotification — the rider's ongoing "live ride" Android notification,
 * rendered with Notifee. ONE notification (fixed id) is shown while a ride is
 * active and updated in place as the status changes; it is cancelled when the
 * ride ends.
 *
 * Two drivers share this one notification id, so they never duplicate:
 *  - foreground / app-alive → useRideStatusNotification (local ride-store state,
 *    rich content incl. OTP) calls showOrUpdate/cancel.
 *  - background / killed → the FCM data handler (app/_layout) calls
 *    handleFcmData() on the backend's data-only `live_activity` message.
 *
 * Replaces the previous expo-notifications renderer. Android-only; Notifee is
 * lazy-required so iOS / Expo Go / web import this module harmlessly (no-op).
 * iOS uses native Live Activities (Phase 3), not this.
 */
import { Platform } from 'react-native';

let notifee: any = null;
let AndroidImportance: any = {};
let AndroidVisibility: any = {};
try {
    // Guarded native-module require — per the file header, must stay
    // runtime require() (not a static import) so iOS/Expo Go/web import
    // this module harmlessly instead of crashing on a missing native binary.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('@notifee/react-native');
    notifee = mod.default ?? mod;
    AndroidImportance = mod.AndroidImportance ?? {};
    AndroidVisibility = mod.AndroidVisibility ?? {};
} catch {
    /* Notifee native module unavailable (iOS-without-build / Expo Go / web) */
}

const NOTIF_ID = 'spinr-ride-live';
export const RIDE_LIVE_CHANNEL = 'ride-live';

const available = () => Boolean(notifee) && Platform.OS === 'android';

let channelReady: Promise<void> | null = null;
function ensureChannel(): Promise<void> {
    if (!channelReady) {
        channelReady = notifee
            .createChannel({
                id: RIDE_LIVE_CHANNEL,
                name: 'Live Ride',
                description: 'Shows your current ride status while a trip is active.',
                // LOW so updating the status text never re-rings the phone — the
                // high-importance "ride-updates" channel handles sound alerts.
                importance: AndroidImportance.LOW,
                vibration: false,
                visibility: AndroidVisibility.PUBLIC,
            })
            .then(() => undefined);
    }
    return channelReady!;
}

export interface RideLiveContent {
    title: string;
    body: string;
    rideId?: string;
}

/** Create or update (same id → in place) the ongoing notification. */
export async function showOrUpdate(content: RideLiveContent): Promise<void> {
    if (!available()) return;
    try {
        await ensureChannel();
        await notifee.displayNotification({
            id: NOTIF_ID,
            title: content.title,
            body: content.body,
            data: { rideId: content.rideId ?? '', type: 'ride_status_live' },
            android: {
                channelId: RIDE_LIVE_CHANNEL,
                importance: AndroidImportance.LOW,
                ongoing: true, // sticky — not swipe-dismissible while the ride is active
                autoCancel: false,
                onlyAlertOnce: true, // updates don't re-alert
                smallIcon: 'ic_launcher',
                pressAction: { id: 'default', launchActivity: 'default' },
                showTimestamp: false,
            },
        });
    } catch (e) {
        console.warn('[RideLive] showOrUpdate failed:', e);
    }
}

/** Cancel the ongoing notification (ride ended / cancelled). */
export async function cancel(): Promise<void> {
    if (!available()) return;
    try {
        await notifee.cancelNotification(NOTIF_ID);
    } catch {
        /* swallow */
    }
}

function etaText(minutes?: string): string {
    const n = minutes ? parseInt(minutes, 10) : NaN;
    if (!n || n <= 0) return '';
    return n === 1 ? '1 min away' : `${n} min away`;
}

/**
 * Route a backend `live_activity` FCM data message to the notification.
 * Returns true if the message was a live-activity message (handled).
 */
export async function handleFcmData(data: Record<string, string> | undefined): Promise<boolean> {
    if (!data || data.type !== 'live_activity') return false;
    if (data.event === 'end') {
        await cancel();
        return true;
    }
    const body = [etaText(data.eta_minutes), data.vehicle_label].filter(Boolean).join(' · ');
    await showOrUpdate({
        title: data.headline || 'Ride update',
        body,
        rideId: data.ride_id,
    });
    return true;
}
