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

// v3: Android channel settings (incl. sound) are IMMUTABLE once created on a
// device. v2 was created pointing at a `ride_offer` raw resource that was
// never bundled (no config plugin copied it), so v2 rings silent forever on
// existing installs. v3 + the withRideOfferSound plugin = audible offers.
// Bump this suffix again any time the channel config changes.
const RIDE_OFFER_CHANNEL_ID = 'ride-offers-v3';
// Foreground variant: shown while the app is OPEN, alongside the in-app
// offer panel. Android sound is channel-level and the in-app MP3 loop
// (useRideOfferSound) is already ringing, so this channel is silent —
// otherwise the driver hears two overlapping loops.
const RIDE_OFFER_SILENT_CHANNEL_ID = 'ride-offers-fg-v1';
const STALE_CHANNEL_IDS = ['ride-offers-v2'];
const RIDE_OFFER_NOTIFICATION_ID = 'ride-offer-current';
const RIDE_OFFER_CATEGORY_ID = 'ride-offer';
const DEFAULT_RIDE_OFFER_TIMEOUT_MS = 15_000;

// Android REJECTS a notification whose small icon can't be resolved — it does
// NOT fall back to the app icon. 'ic_launcher' is the one drawable Expo
// generates into every Android build (Notifee resolves the name against both
// mipmap and drawable), so it's the only small-icon name guaranteed to exist.
// The previous 'ic_notification' was never produced by any config plugin, so
// every displayNotification() threw and ride offers silently failed to appear.
// (Swap in a dedicated monochrome glyph later via the expo-notifications
// plugin — that's cosmetic; this is what makes the offer render at all.)
const RIDE_OFFER_SMALL_ICON = 'ic_launcher';

export const NOTIFEE_RIDE_OFFER_CHANNEL_ID = RIDE_OFFER_CHANNEL_ID;
export const NOTIFEE_RIDE_OFFER_NOTIFICATION_ID = RIDE_OFFER_NOTIFICATION_ID;

export type NotifeeAction = 'accept' | 'decline';

export interface RideOfferDisplayData {
    ride_id: string;
    booking_id?: string;
    pickup_address?: string;
    dropoff_address?: string;
    fare: number;
    total_bonus?: number;
    distance_km?: number;
    duration_minutes?: number;
    surge_multiplier?: number;
    rider_name?: string;
    rider_rating?: number;
    incentives_count?: number;
    countdown_seconds?: number;
    offer_expires_at?: string;
    // Signed, short-lived URL to the branded fare-banner image. When present,
    // the Android card expands to this rich BigPicture instead of the text card.
    offer_card_url?: string;
}

let channelReadyPromise: Promise<void> | null = null;
let rideOfferDismissTimer: ReturnType<typeof setTimeout> | null = null;

function getRideOfferTimeoutMs(offer: RideOfferDisplayData): number {
    if (offer.offer_expires_at) {
        const expiresAtMs = new Date(offer.offer_expires_at).getTime();
        if (Number.isFinite(expiresAtMs)) {
            return Math.max(0, expiresAtMs - Date.now());
        }
    }

    if (typeof offer.countdown_seconds === 'number' && offer.countdown_seconds > 0) {
        return offer.countdown_seconds * 1000;
    }

    return DEFAULT_RIDE_OFFER_TIMEOUT_MS;
}

function scheduleRideOfferDismiss(timeoutMs: number): void {
    if (rideOfferDismissTimer) {
        clearTimeout(rideOfferDismissTimer);
    }

    rideOfferDismissTimer = setTimeout(() => {
        rideOfferDismissTimer = null;
        notifee.cancelNotification(RIDE_OFFER_NOTIFICATION_ID).catch(() => undefined);
    }, timeoutMs);
}

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

            await notifee.createChannel({
                id: RIDE_OFFER_SILENT_CHANNEL_ID,
                name: 'Ride Offers (in-app)',
                description: 'New ride requests while the app is open — sound comes from the app itself',
                importance: AndroidImportance.HIGH,
                vibration: true,
                vibrationPattern: [300, 500, 300, 500],
                visibility: AndroidVisibility.PUBLIC,
            });

            // Remove superseded channels so drivers don't see dead duplicates
            // under Settings → Notifications.
            for (const staleId of STALE_CHANNEL_IDS) {
                await notifee.deleteChannel(staleId).catch(() => undefined);
            }

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
                            title: 'Approve',
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
 *
 * `silent: true` is the foreground variant (app open, in-app panel already
 * visible and the MP3 loop already playing): banner + vibration only, no
 * channel sound, no full-screen intent.
 *
 * `muted: true` suppresses AUDIO ONLY (driver turned off Settings → Sound &
 * Haptics → Sound Effects): no channel sound / APNs sound / loop, but the
 * heads-up card and full-screen wake still fire — the driver opted out of
 * noise, not of seeing offers.
 */
export async function displayRideOfferNotification(
    offer: RideOfferDisplayData,
    opts?: { silent?: boolean; muted?: boolean },
): Promise<void> {
    await ensureNotifeeReady();
    const silent = opts?.silent === true;
    // Everything audible keys off `muted`; visibility behaviour keys off `silent`.
    const muted = silent || opts?.muted === true;

    const timeoutMs = getRideOfferTimeoutMs(offer);
    if (timeoutMs <= 0) {
        await dismissRideOfferNotification();
        return;
    }

    const totalEarnings = offer.fare + (offer.total_bonus || 0);

    // Title = the always-visible line (collapsed shade + heads-up header). Lead
    // with the money — that's the driver's accept/decline signal. Surge is
    // deliberately not shown; the fare already reflects it. NEVER the booking
    // UUID; it's opaque noise that was burying the addresses on the one line.
    const title = `New ride · $${totalEarnings.toFixed(2)}`;

    // Trip summary: distance • time • rider (with rating when known).
    const tripStats = [
        offer.distance_km != null ? `${offer.distance_km.toFixed(1)} km` : null,
        offer.duration_minutes != null ? `${Math.round(offer.duration_minutes)} min` : null,
    ].filter(Boolean).join(' • ');
    const riderLabel = offer.rider_name
        ? `${offer.rider_name}${offer.rider_rating ? ` ${offer.rider_rating.toFixed(1)}★` : ''}`
        : null;
    const summaryLine = [tripStats || null, riderLabel].filter(Boolean).join('  ·  ');
    const bonusLine = offer.total_bonus && offer.total_bonus > 0
        ? `🎁 +$${offer.total_bonus.toFixed(2)} bonus` : null;

    // Body (expanded BIG_TEXT): pickup → dropoff → trip stats → bonus. Labeled
    // with glyphs so the driver scans the route at a glance. The booking id
    // lives only in the data payload for the app to read on tap.
    const body = [
        offer.pickup_address ? `📍 ${offer.pickup_address}` : null,
        offer.dropoff_address ? `🏁 ${offer.dropoff_address}` : null,
        summaryLine || null,
        bonusLine,
    ].filter(Boolean).join('\n') || 'Tap to view ride details';

    const dataPayload: Record<string, string> = {
        ride_id: offer.ride_id,
        booking_id: offer.booking_id || offer.ride_id,
        type: 'new_ride_assignment',
    };

    const request = {
        id: RIDE_OFFER_NOTIFICATION_ID,
        title,
        body,
        // Sub-label under the title (Android header sub-text / iOS subtitle line)
        // — the trip at a glance without expanding.
        subtitle: summaryLine || undefined,
        data: dataPayload,
        android: {
            channelId: muted ? RIDE_OFFER_SILENT_CHANNEL_ID : RIDE_OFFER_CHANNEL_ID,
            category: AndroidCategory.CALL,
            importance: AndroidImportance.HIGH,
            visibility: AndroidVisibility.PUBLIC,
            color: AndroidColor.GREEN,
            colorized: true,
            smallIcon: RIDE_OFFER_SMALL_ICON,
            // Branded mark on the right of the card — turns a plain text row
            // into a recognizable Spinr offer at a glance. Statically required
            // (bundled), so it resolves even in the headless background launch.
            largeIcon: require('../assets/images/icon.png'),
            circularLargeIcon: true,
            ongoing: true,
            autoCancel: false,
            showTimestamp: true,
            ...(muted ? {} : { sound: 'ride_offer' }),
            vibrationPattern: [300, 500, 300, 500],
            // Rich card: when the backend handed us a signed banner URL, expand
            // to the BigPicture fare card; otherwise fall back to BigText. If
            // the image fails to load, Android degrades BigPicture to the
            // title/body row on its own, so the offer is never lost.
            style: (offer.offer_card_url
                ? {
                    type: 0, // BIG_PICTURE
                    picture: offer.offer_card_url,
                    title: title,
                    ...(summaryLine ? { summary: summaryLine } : {}),
                }
                : {
                    type: 1, // BIG_TEXT
                    title: title,
                    text: body,
                    ...(summaryLine ? { summary: summaryLine } : {}),
                }) as any,
            actions: [
                {
                    title: '<b><font color="#00D26A">APPROVE</font></b>',
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
            // Skipped for the foreground variant: the app is already on
            // screen and relaunching the activity would jolt the driver.
            ...(silent ? {} : { fullScreenAction: { id: 'default', launchActivity: 'default' } }),
            // Heads-up takes priority over silent notifications
            asForegroundService: false,
            loopSound: !muted,
            timeoutAfter: timeoutMs,
        },
        ios: {
            categoryId: RIDE_OFFER_CATEGORY_ID,
            critical: false, // requires special Apple entitlement
            ...(muted ? {} : { sound: 'ride_offer.caf' }),
            interruptionLevel: 'timeSensitive' as const, // iOS 15+
            foregroundPresentationOptions: {
                alert: true,
                sound: !muted,
                badge: true,
                banner: true,
                list: true,
            },
        },
    };

    try {
        await notifee.displayNotification(request);
    } catch (e) {
        // Guardrail: a single unresolved resource (small icon, custom sound,
        // BIG_TEXT style, full-screen intent) makes Android reject the WHOLE
        // notification — the driver would see nothing and miss the fare. Never
        // let that happen silently. Log loudly, then retry with a minimal,
        // dependency-free notification that still carries Accept/Decline so the
        // offer stays actionable. Channel sound (set at channel creation) still
        // rings; we just drop the per-notification overrides that can throw.
        console.error(
            '[Notifee] rich ride-offer render failed — falling back to a basic notification:',
            e,
        );
        try {
            await notifee.displayNotification({
                id: RIDE_OFFER_NOTIFICATION_ID,
                title,
                body,
                data: dataPayload,
                android: {
                    channelId: muted ? RIDE_OFFER_SILENT_CHANNEL_ID : RIDE_OFFER_CHANNEL_ID,
                    importance: AndroidImportance.HIGH,
                    smallIcon: RIDE_OFFER_SMALL_ICON,
                    pressAction: { id: 'default', launchActivity: 'default' },
                    actions: [
                        { title: 'APPROVE', pressAction: { id: 'accept', launchActivity: 'default' } },
                        { title: 'Decline', pressAction: { id: 'decline' } },
                    ],
                    timeoutAfter: timeoutMs,
                },
                ios: {
                    categoryId: RIDE_OFFER_CATEGORY_ID,
                    interruptionLevel: 'timeSensitive' as const,
                },
            });
        } catch (e2) {
            // Both renders failed — surface it; the WS in-app panel is the only
            // remaining channel and the backend offer-timeout still protects the
            // driver's miss streak.
            console.error('[Notifee] basic ride-offer render also failed:', e2);
        }
    }
    scheduleRideOfferDismiss(timeoutMs);
}

/**
 * Dismiss the active ride-offer notification — call this when the offer
 * is accepted, declined, expired, or cancelled so it doesn't linger.
 */
export async function dismissRideOfferNotification(): Promise<void> {
    if (rideOfferDismissTimer) {
        clearTimeout(rideOfferDismissTimer);
        rideOfferDismissTimer = null;
    }
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
