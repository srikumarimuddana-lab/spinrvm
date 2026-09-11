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
//
// fg-v2: DEFAULT importance, no vibration. fg-v1 was HIGH with a vibration
// pattern, which was harmless while this channel was only ever reached by an
// *update* to an already-posted notification. It is not harmless now that the
// handover below cancels first and posts a NEW notification: HIGH peeks a
// heads-up banner over the offer panel the driver is already looking at, and
// re-buzzes on every foreground. The in-app paths vibrate explicitly
// (useDriverDashboard.ts), so the channel must not.
// Channel config is immutable once created on a device (see the v3 note
// above), so this needed a new id — editing fg-v1 would have been a silent
// no-op on every install that already has it.
const RIDE_OFFER_SILENT_CHANNEL_ID = 'ride-offers-fg-v2';
const STALE_CHANNEL_IDS = ['ride-offers-v2', 'ride-offers-fg-v1'];
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
// Absolute deadline for the offer currently on screen, pinned the first time
// we see that ride. See getRideOfferTimeoutMs.
let rideOfferDeadline: { rideId: string; expiresAtMs: number } | null = null;

function getRideOfferTimeoutMs(offer: RideOfferDisplayData): number {
    // `offer_expires_at` is already absolute, so a re-post recomputes the same
    // deadline and needs no memory. The other two branches are RELATIVE, and a
    // handover re-posts this same notification id mid-offer — so recomputing
    // them would hand the card a fresh countdown_seconds (or a fresh 15s) on
    // every handover and the card would outlive the backend's offer. Pin them
    // to an absolute deadline on first sight of the ride and reuse it.
    if (offer.offer_expires_at) {
        const expiresAtMs = new Date(offer.offer_expires_at).getTime();
        if (Number.isFinite(expiresAtMs)) {
            rideOfferDeadline = { rideId: offer.ride_id, expiresAtMs };
            return Math.max(0, expiresAtMs - Date.now());
        }
    }

    if (rideOfferDeadline && rideOfferDeadline.rideId === offer.ride_id) {
        return Math.max(0, rideOfferDeadline.expiresAtMs - Date.now());
    }

    const relativeMs = typeof offer.countdown_seconds === 'number' && offer.countdown_seconds > 0
        ? offer.countdown_seconds * 1000
        : DEFAULT_RIDE_OFFER_TIMEOUT_MS;
    rideOfferDeadline = { rideId: offer.ride_id, expiresAtMs: Date.now() + relativeMs };
    return relativeMs;
}

function scheduleRideOfferDismiss(timeoutMs: number): void {
    if (rideOfferDismissTimer) {
        clearTimeout(rideOfferDismissTimer);
    }

    rideOfferDismissTimer = setTimeout(() => {
        rideOfferDismissTimer = null;
        // Clear the pinned deadline on the auto-dismiss path too, not just in
        // dismissRideOfferNotification(). Otherwise an already-elapsed deadline
        // survives for this ride_id, and a later post for the SAME ride — which
        // utils/push_retry.py does on a dispatch retry — resolves a timeout of 0
        // and is dismissed before it ever renders. Only bites a payload with no
        // `offer_expires_at` (the absolute branch recomputes and overwrites).
        rideOfferDeadline = null;
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
                importance: AndroidImportance.DEFAULT,
                vibration: false,
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
    })().catch((e) => {
        // Never cache a failure. A single transient createChannel /
        // requestPermission error used to disable every later notification for
        // the whole process lifetime, because the rejected promise stayed in
        // the cache and every subsequent call re-awaited it. That was already
        // wrong; it is worse now that displayRideOfferNotification's handover
        // depends on being reachable in order to go SILENT, so a poisoned cache
        // would mean a ride-offer ringtone nothing can stop.
        channelReadyPromise = null;
        throw e;
    });
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
 *
 * `reclaim: true` is the reverse handover: the app is going to the background
 * mid-offer, so the OS notification has to take the ring BACK (expo-audio
 * pauses the in-app player on the background transition, leaving the driver
 * with nothing). Loud like a fresh offer, but without the full-screen intent —
 * relaunching the activity the driver just left would be hostile.
 */
export async function displayRideOfferNotification(
    offer: RideOfferDisplayData,
    opts?: { silent?: boolean; muted?: boolean; reclaim?: boolean },
): Promise<void> {
    const silent = opts?.silent === true;
    // Everything audible keys off `muted`; visibility behaviour keys off `silent`.
    const muted = silent || opts?.muted === true;
    const reclaim = opts?.reclaim === true;

    // HANDOVER, not an update — this is the fix for the overlapping ringtones.
    //
    // A loud notification already on screen is looping its channel ringtone via
    // FLAG_INSISTENT, which @notifee/react-native documents as repeating "until
    // the notification is cancelled or the notification window is opened"
    // (src/types/NotificationAndroid.ts, AndroidFlags). Re-posting the same id
    // on the silent channel is an UPDATE, and nothing in Notifee's contract
    // says an update stops an in-flight insistent ring. (Stock AOSP's
    // buzzBeepBlinkLocked may clear it via clearSoundLocked(); that is an
    // undocumented implementation detail and OEM-dependent, so it is not
    // something the driver's alert can rest on.) Cancel explicitly instead.
    //
    // Both handover directions cancel first, so the channel actually changes
    // rather than relying on an update being honoured: `silent` hands the ring
    // to the in-app MP3 loop, `reclaim` hands it back to the OS.
    //
    // Deliberately ABOVE ensureNotifeeReady(): cancelling needs no channel, and
    // hoisting it means a slow cold-start setup (two createChannel calls, N
    // deleteChannel calls and requestPermission) cannot hold the ring open.
    // Guarded on `silent`/`reclaim`, never on `muted`: a muted driver still
    // gets the card, and there is no ring to hand over.
    // Failure is swallowed — if there was nothing to cancel, or the native call
    // is in a bad state, the post below must still run.
    if ((silent || reclaim) && Platform.OS === 'android') {
        try {
            await notifee.cancelNotification(RIDE_OFFER_NOTIFICATION_ID);
        } catch {
            /* nothing posted yet, or a bad native state — the post still runs */
        }
    }

    // A setup failure must NOT abort the post, now that the cancel above has
    // already run. Android channels are created once and persist on the device,
    // so a transient failure here (native error, permission race) usually still
    // leaves a postable channel — whereas aborting leaves the driver with no
    // card at all. For `reclaim` that is outright SILENCE: the in-app tone has
    // already been stopped by the caller and this post is the only remaining
    // alert. Log loudly rather than swallow (CLAUDE.md), then try anyway; the
    // two-tier fallback around displayNotification below covers a genuinely
    // unpostable state.
    try {
        await ensureNotifeeReady();
    } catch (e) {
        console.error(
            '[Notifee] channel/permission setup failed — posting the ride offer anyway:',
            e,
        );
    }

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
            // Also skipped for `reclaim`: the driver just backgrounded the app
            // themselves, and slamming the activity back would fight them.
            ...(silent || reclaim ? {} : { fullScreenAction: { id: 'default', launchActivity: 'default' } }),
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
    // The offer is over, so the pinned deadline must not leak into the next one
    // (a different ride_id would ignore it anyway, but a re-offer of the SAME
    // ride would otherwise inherit the old, already-elapsed deadline and the
    // card would be dismissed instantly).
    rideOfferDeadline = null;
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
