/**
 * Notifee wrapper for ride-offer "incoming call" style notifications.
 *
 * Why Notifee over expo-notifications:
 *   - Full-screen intent (Android): wakes the screen and slams the app
 *     into foreground when an offer arrives even if the device is locked
 *     or the app is killed. This is what Uber/Lyft use.
 *   - Heads-up notification with Accept/Decline action buttons.
 *   - Custom looping ringtone via a raw sound resource.
 *   - Channel-level importance (HIGH) so the OS doesn't downgrade us.
 *
 * iOS limitations: full-screen intent does not exist on iOS. We still get
 * a richer banner with action buttons via UNNotificationCategory.
 *
 * Background-tap and action-button handling lives in _layout.tsx via
 * notifee.onBackgroundEvent() — that must be registered at module top level.
 */

import notifee, {
    AndroidCategory,
    AndroidColor,
    AndroidImportance,
    AndroidVisibility,
    EventType,
    type Event,
} from '@notifee/react-native';
import { Platform } from 'react-native';

const RIDE_OFFER_CHANNEL_ID = 'ride-offers-v2';
const RIDE_OFFER_NOTIFICATION_ID = 'ride-offer-current';
const RIDE_OFFER_CATEGORY_ID = 'ride-offer';

export const NOTIFEE_RIDE_OFFER_CHANNEL_ID = RIDE_OFFER_CHANNEL_ID;
export const NOTIFEE_RIDE_OFFER_NOTIFICATION_ID = RIDE_OFFER_NOTIFICATION_ID;

export type NotifeeAction = 'accept' | 'decline';

export interface RideOfferDisplayData {
    ride_id: string;
    pickup_address?: string;
    dropoff_address?: string;
    fare: number;
    total_bonus?: number;
    distance_km?: number;
    duration_minutes?: number;
    surge_multiplier?: number;
    rider_name?: string;
    incentives_count?: number;
}

let channelReadyPromise: Promise<void> | null = null;

/**
 * Idempotent — creates the high-importance "ride-offers" Android channel
 * and registers the iOS notification category with Accept/Decline buttons.
 * Safe to call on every cold start; Notifee dedupes by channel id.
 */
export async function ensureNotifeeReady(): Promise<void> {
    if (channelReadyPromise) return channelReadyPromise;
    channelReadyPromise = (async () => {
        if (Platform.OS === 'android') {
            // High importance + bypass DND so a fare offer interrupts
            // even when the driver has Do Not Disturb on.
            await notifee.createChannel({
                id: RIDE_OFFER_CHANNEL_ID,
                name: 'Ride Offers',
                description: 'New ride requests — wakes the screen like an incoming call',
                importance: AndroidImportance.HIGH,
                sound: 'ride_offer',
                vibration: true,
                vibrationPattern: [300, 500, 300, 500],
                lights: true,
                lightColor: AndroidColor.GREEN,
                bypassDnd: true,
                visibility: AndroidVisibility.PUBLIC,
            });

            // Ask for POST_NOTIFICATIONS on Android 13+. No-op on older OS.
            await notifee.requestPermission();
        } else {
            // iOS: provisional + critical (if entitled). Critical alerts
            // require a special Apple entitlement; we attempt and fall
            // back gracefully if denied.
            await notifee.requestPermission({
                alert: true,
                sound: true,
                badge: true,
                provisional: false,
            });

            await notifee.setNotificationCategories([
                {
                    id: RIDE_OFFER_CATEGORY_ID,
                    actions: [
                        {
                            id: 'accept',
                            title: 'Accept',
                            foreground: true,
                            authenticationRequired: false,
                        },
                        {
                            id: 'decline',
                            title: 'Decline',
                            destructive: true,
                            foreground: false,
                        },
                    ],
                    allowInCarPlay: false,
                    intentIdentifiers: [],
                },
            ]);
        }
    })();
    return channelReadyPromise;
}

/**
 * Display the heads-up / full-screen ride offer.
 *
 * On Android: shows a heads-up banner immediately; if the phone is locked
 * or the app is killed, the fullScreenAction wakes the screen and routes
 * the user straight into the offer panel.
 *
 * On iOS: shows a rich notification with Accept/Decline buttons.
 */
export async function displayRideOfferNotification(
    offer: RideOfferDisplayData,
): Promise<void> {
    await ensureNotifeeReady();

    const totalEarnings = offer.fare + (offer.total_bonus || 0);
    const bonusBadge = offer.total_bonus && offer.total_bonus > 0
        ? ` (+$${offer.total_bonus.toFixed(2)} bonus)` : '';
    const surgeBadge = offer.surge_multiplier && offer.surge_multiplier > 1
        ? ` ${offer.surge_multiplier.toFixed(1)}× SURGE` : '';

    const title = `$${totalEarnings.toFixed(2)}${surgeBadge} — New ride`;
    const body = [
        offer.pickup_address ? `From: ${offer.pickup_address}` : null,
        offer.dropoff_address ? `To: ${offer.dropoff_address}` : null,
        offer.distance_km && offer.duration_minutes
            ? `${offer.distance_km.toFixed(1)} km • ${Math.round(offer.duration_minutes)} min${bonusBadge}`
            : null,
    ].filter(Boolean).join('\n');

    const dataPayload: Record<string, string> = {
        ride_id: offer.ride_id,
        type: 'new_ride_assignment',
    };

    await notifee.displayNotification({
        id: RIDE_OFFER_NOTIFICATION_ID,
        title,
        body,
        data: dataPayload,
        android: {
            channelId: RIDE_OFFER_CHANNEL_ID,
            category: AndroidCategory.CALL,
            importance: AndroidImportance.HIGH,
            visibility: AndroidVisibility.PUBLIC,
            color: AndroidColor.GREEN,
            colorized: true,
            smallIcon: 'ic_notification', // fallback to app icon if missing
            largeIcon: undefined,
            ongoing: true,
            autoCancel: false,
            showTimestamp: true,
            sound: 'ride_offer',
            vibrationPattern: [300, 500, 300, 500],
            style: {
                type: 1, // BIG_TEXT
                text: body,
            } as any,
            actions: [
                {
                    title: '<b><font color="#00D26A">ACCEPT</font></b>',
                    pressAction: { id: 'accept', launchActivity: 'default' },
                },
                {
                    title: '<font color="#FF3B30">Decline</font>',
                    pressAction: { id: 'decline' },
                },
            ],
            pressAction: { id: 'default', launchActivity: 'default' },
            // Full-screen intent — wakes the screen and shows the app
            // immediately, even if the phone is locked. Requires
            // USE_FULL_SCREEN_INTENT permission (added by config plugin).
            fullScreenAction: { id: 'default', launchActivity: 'default' },
            // Heads-up takes priority over silent notifications
            asForegroundService: false,
            loopSound: true,
            timeoutAfter: 30_000, // auto-clear after 30s if untouched
        },
        ios: {
            categoryId: RIDE_OFFER_CATEGORY_ID,
            critical: false, // requires special Apple entitlement
            sound: 'ride_offer.caf',
            interruptionLevel: 'timeSensitive', // iOS 15+
        },
    });
}

/**
 * Dismiss the active ride-offer notification — call this when the offer
 * is accepted, declined, expired, or cancelled so it doesn't linger.
 */
export async function dismissRideOfferNotification(): Promise<void> {
    try {
        await notifee.cancelNotification(RIDE_OFFER_NOTIFICATION_ID);
    } catch {
        /* swallow — nothing to dismiss is fine */
    }
}

/**
 * Map a Notifee event (foreground or background) to a structured action.
 * Returns null if the event isn't actionable.
 */
export function parseRideOfferEvent(
    event: Event,
): { action: NotifeeAction | 'tap'; ride_id?: string } | null {
    const { type, detail } = event;
    const data = detail?.notification?.data || {};
    if (data.type !== 'new_ride_assignment') return null;
    const ride_id = typeof data.ride_id === 'string' ? data.ride_id : undefined;

    if (type === EventType.ACTION_PRESS) {
        const id = detail.pressAction?.id;
        if (id === 'accept') return { action: 'accept', ride_id };
        if (id === 'decline') return { action: 'decline', ride_id };
    }
    if (type === EventType.PRESS) {
        return { action: 'tap', ride_id };
    }
    return null;
}
